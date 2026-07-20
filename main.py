"""
TikTok Trend Bot — персональний трендовий дайджест у Telegram + AI чат.
Apify (скрейпінг TikTok) -> Claude (фільтр + розбір) -> Telegram.

Команди:
  /start     — привітання + головне меню (і твій chat_id для CHAT_ID)
  /niche     — вибрати нішу + конкретні хештеги + регіон
  /digest    — дайджест прямо зараз
  /settings  — налаштування
  /ask       — запитати Claude про тренди (контекст з твоєї ніші)
  /cancel    — вийти з режиму запитань
Щодня о DIGEST_HOUR (UTC) шле дайджест автоматично.

Стан (ніша, хештеги, регіон, показані відео, кеш пулу трендів)
зберігається в SQLite (bot.db) і переживає рестарти Railway.
"""

import asyncio
import json
import logging
import os
from html import escape
from datetime import datetime, timedelta, timezone, time as dtime

import anthropic
import httpx
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("trendbot")

# ---- Конфіг через змінні середовища (Railway -> Variables) ----
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
APIFY_TOKEN = os.environ["APIFY_TOKEN"]
CHAT_ID = os.environ.get("CHAT_ID")
HASHTAGS = [h.strip() for h in os.environ.get("HASHTAGS", "football,beauty").split(",")]
RESULTS_PER_TAG = int(os.environ.get("RESULTS_PER_TAG", "20"))
DIGEST_HOUR = int(os.environ.get("DIGEST_HOUR", "8"))
POOL_SIZE = int(os.environ.get("POOL_SIZE", "30"))          # скільки відео тримаємо в кеш-пулі
POOL_TTL_HOURS = int(os.environ.get("POOL_TTL_HOURS", "3"))  # коли пул вважати застарілим
BATCH_SIZE = 5                                               # скільки відео в одному дайджесті

APIFY_ACTOR = "clockworks~tiktok-scraper"
BANNER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "welcome.png")
MENU_BUTTON_TEXT = "🏠 Меню"

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ---- Ніші ----
NICHES = {
    "football": {"hashtags": ["football", "soccer", "futbol"], "emoji": "⚽"},
    "beauty": {"hashtags": ["beauty", "makeup", "skincare"], "emoji": "💄"},
    "fitness": {"hashtags": ["fitness", "gym", "workout"], "emoji": "💪"},
    "dance": {"hashtags": ["dance", "tiktokdance", "choreography"], "emoji": "💃"},
    "cooking": {"hashtags": ["cooking", "recipe", "food"], "emoji": "🍳"},
    "gaming": {"hashtags": ["gaming", "gamer", "esports"], "emoji": "🎮"},
    "travel": {"hashtags": ["travel", "adventure", "tourism"], "emoji": "✈️"},
    "fashion": {"hashtags": ["fashion", "style", "outfit"], "emoji": "👗"},
}

# ---- Регіони (гео через Apify proxy; впливає на видачу, але це не офіційний trending API) ----
REGIONS = {
    "global": {"label": "🌍 Global", "code": None},
    "ua": {"label": "🇺🇦 Україна", "code": "UA"},
    "us": {"label": "🇺🇸 США", "code": "US"},
    "gb": {"label": "🇬🇧 UK", "code": "GB"},
    "pl": {"label": "🇵🇱 Польща", "code": "PL"},
    "de": {"label": "🇩🇪 Німеччина", "code": "DE"},
    "br": {"label": "🇧🇷 Бразилія", "code": "BR"},
    "id": {"label": "🇮🇩 Індонезія", "code": "ID"},
}

MUSIC_STYLES = {
    "energy": "🎧 Енергійна / під рекламу",
    "emotional": "🎻 Серйозна / емоційна",
    "rap": "🔥 Хайповий реп-драйв",
    "any": "⭐ Без фільтра стилю",
}


# ---------------- Хелпери налаштувань ----------------
def resolve_hashtags(prefs: dict) -> list[str]:
    """Хештеги користувача: свій вибір -> хештеги ніші -> дефолт з env."""
    if prefs.get("hashtags"):
        return prefs["hashtags"]
    niche = prefs.get("niche_key")
    if niche in NICHES:
        return NICHES[niche]["hashtags"]
    return HASHTAGS


def pool_niche_key(prefs: dict) -> str:
    return prefs.get("niche_key") or "default"


def region_label(region: str) -> str:
    return REGIONS.get(region, REGIONS["global"])["label"]


# ---------------- Клавіатури ----------------
def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Вибрати нішу", callback_data="niche_menu")],
        [InlineKeyboardButton("🌍 Регіон трендів", callback_data="region_menu")],
        [InlineKeyboardButton("📊 Дайджест зараз", callback_data="digest_now")],
        [InlineKeyboardButton("🎵 Музика в тренді", callback_data="music_digest")],
        [InlineKeyboardButton("💬 Запитати Claude", callback_data="ask_mode")],
    ])


def whats_next_keyboard(niche_key: str) -> InlineKeyboardMarkup:
    """Рядок 'що далі' під дайджестами і відповідями."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Далі", callback_data=f"next_{niche_key}")],
        [InlineKeyboardButton("📋 Ніша", callback_data="niche_menu"),
         InlineKeyboardButton("🌍 Регіон", callback_data="region_menu")],
        [InlineKeyboardButton("🎵 Музика в тренді", callback_data="music_digest"),
         InlineKeyboardButton("💬 Запитати Claude", callback_data="ask_mode")],
    ])


def niche_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = []
    for niche_key, niche_data in NICHES.items():
        emoji = niche_data["emoji"]
        keyboard.append([InlineKeyboardButton(
            f"{emoji} {niche_key.capitalize()}", callback_data=f"select_niche_{niche_key}"
        )])
    return InlineKeyboardMarkup(keyboard)


def region_menu_keyboard() -> InlineKeyboardMarkup:
    keys = list(REGIONS)
    rows = []
    for i in range(0, len(keys), 2):
        rows.append([
            InlineKeyboardButton(REGIONS[k]["label"], callback_data=f"select_region_{k}")
            for k in keys[i:i + 2]
        ])
    return InlineKeyboardMarkup(rows)


def hashtag_keyboard(niche_key: str, selected: set[int]) -> InlineKeyboardMarkup:
    tags = NICHES[niche_key]["hashtags"]
    rows = []
    for i, tag in enumerate(tags):
        mark = "✅" if i in selected else "▫️"
        rows.append([InlineKeyboardButton(f"{mark} #{tag}", callback_data=f"ht_t_{niche_key}_{i}")])
    rows.append([InlineKeyboardButton("✅ Готово", callback_data=f"ht_done_{niche_key}")])
    return InlineKeyboardMarkup(rows)


def digest_only_keyboard() -> InlineKeyboardMarkup:
    """Єдина кнопка в кінці флоу ніша→хештеги→регіон."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Дайджест зараз", callback_data="digest_now")],
    ])


def flow_summary_text(prefs: dict) -> str:
    """Підсумок поточного вибору юзера після кроку регіону."""
    if prefs.get("niche_key") in NICHES:
        niche_data = NICHES[prefs["niche_key"]]
        niche_label = f"{niche_data['emoji']} {prefs['niche_key'].capitalize()}"
    else:
        niche_label = "не вибрана (дефолтна)"
    hashtags_label = ", ".join(f"#{h}" for h in resolve_hashtags(prefs))
    return (
        f"✅ Ніша: {niche_label}, хештеги: {hashtags_label}, "
        f"регіон: {region_label(prefs['region'])}.\n"
        "Тисни «📊 Дайджест зараз», щоб отримати перші 5 відео."
    )


def music_style_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=f"music_style_{key}")]
         for key, label in MUSIC_STYLES.items()]
    )


def menu_reply_keyboard() -> ReplyKeyboardMarkup:
    """Постійна кнопка '🏠 Меню' внизу екрана."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton(MENU_BUTTON_TEXT)]],
        resize_keyboard=True,
        is_persistent=True,
    )


# ---------------- Apify ----------------
async def fetch_tiktoks(hashtags: list[str], region: str = "global") -> list[dict]:
    """Тягне свіжі відео по хештегах через Apify (гео — через проксі обраної країни)."""
    url = (
        f"https://api.apify.com/v2/acts/{APIFY_ACTOR}"
        f"/run-sync-get-dataset-items?token={APIFY_TOKEN}"
    )
    payload = {
        "hashtags": hashtags,
        "resultsPerPage": RESULTS_PER_TAG,
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": True,
    }
    country = REGIONS.get(region, REGIONS["global"])["code"]
    if country:
        payload["proxyConfiguration"] = {
            "useApifyProxy": True,
            "apifyProxyCountryCode": country,
        }
    async with httpx.AsyncClient(timeout=300) as http:
        r = await http.post(url, json=payload)
        r.raise_for_status()
        items = r.json()
    log.info("Apify: отримано %d відео (region=%s)", len(items), region)
    return items


async def fetch_tiktoks_safe(hashtags: list[str], region: str) -> tuple[list[dict], bool]:
    """Apify з регіоном; якщо country/residential proxy недоступний на плані —
    не падаємо, а повторюємо запит без proxyCountryCode (Global).
    Повертає (items, fell_back_to_global)."""
    try:
        return await fetch_tiktoks(hashtags, region), False
    except Exception as e:
        if region == "global" or not REGIONS.get(region, {}).get("code"):
            raise
        log.warning("Apify з регіоном %s впав (%s) — повторюю як Global", region, e)
        return await fetch_tiktoks(hashtags, "global"), True


def velocity_score(item: dict) -> float:
    """Перегляди на годину з моменту публікації — головний сигнал віральності."""
    plays = item.get("playCount") or 0
    created = item.get("createTimeISO")
    if not created:
        return 0.0
    try:
        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    hours = max((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 1)
    return plays / hours


def prefilter(items: list[dict], top_n: int = 15) -> list[dict]:
    """Топ-N по velocity, компактні поля для Claude + музичні метадані для 🎵."""
    ranked = sorted(items, key=velocity_score, reverse=True)[:top_n]
    slim = []
    for it in ranked:
        music = it.get("musicMeta") or {}
        slim.append({
            "url": it.get("webVideoUrl") or it.get("shareUrl", ""),
            "cover": it.get("covers", {}).get("high", ""),
            "desc": (it.get("text") or "")[:200],
            "plays": it.get("playCount", 0),
            "likes": it.get("diggCount", 0),
            "shares": it.get("shareCount", 0),
            "comments": it.get("commentCount", 0),
            "created": it.get("createTimeISO", ""),
            "velocity_per_hour": round(velocity_score(it)),
            "author": (it.get("authorMeta") or {}).get("name", ""),
            "musicId": str(music.get("musicId") or ""),
            "musicName": music.get("musicName", ""),
            "musicAuthor": music.get("musicAuthor", ""),
            "musicPlayUrl": music.get("playUrl", ""),
        })
    return slim


# ---------------- Пул трендів (кеш, щоб не палити Apify-кредити) ----------------
async def ensure_pool(chat_id: int, niche_key: str, hashtags: list[str],
                      region: str, force: bool = False) -> tuple[list[dict], bool]:
    """Повертає (пул відео, чи був фолбек на Global): з кешу, якщо він свіжий,
    інакше — новий Apify run."""
    if not force:
        cached = await db.get_pool(chat_id, niche_key, region)
        if cached:
            videos, fetched_at = cached
            age = datetime.now(timezone.utc) - fetched_at
            if videos and age < timedelta(hours=POOL_TTL_HOURS):
                log.info("Pool cache hit: chat=%s niche=%s region=%s", chat_id, niche_key, region)
                return videos, False
    items, fell_back = await fetch_tiktoks_safe(hashtags, region)
    videos = prefilter(items, top_n=POOL_SIZE)
    await db.save_pool(chat_id, niche_key, region, videos)
    return videos, fell_back


async def notify_region_fallback(context: ContextTypes.DEFAULT_TYPE, chat_id: int, region: str):
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Регіон {region_label(region)} поки недоступний на нашому Apify-плані, "
             "показую Global 🌍",
    )


async def pool_is_fresh(chat_id: int, prefs: dict) -> bool:
    cached = await db.get_pool(chat_id, pool_niche_key(prefs), prefs["region"])
    if not cached:
        return False
    videos, fetched_at = cached
    return bool(videos) and (datetime.now(timezone.utc) - fetched_at) < timedelta(hours=POOL_TTL_HOURS)


# ---------------- Claude ----------------
def claude_rank(videos: list[dict]) -> list[dict]:
    """Claude вибирає топ-5 і пояснює, чому віральне і що вкрасти."""
    rank_view = [{k: v for k, v in vid.items() if not k.startswith("music")} for vid in videos]
    prompt = f"""Ти — аналітик віральних TikTok-відео для відеомонтажера,
який робить рекламні та UGC-ролики.

Ось дані про відео (velocity_per_hour = перегляди/годину — головний сигнал):

{json.dumps(rank_view, ensure_ascii=False, indent=1)}

Вибери {BATCH_SIZE} найперспективніших для аналізу і натхнення.
Відповідай ТІЛЬКИ валідним JSON-масивом без markdown, формат:
[{{"url": "...", "cover": "...", "why": "1 речення чому віральне (хук/структура/звук/емоція)",
"steal": "1 речення що конкретно вкрасти для своїх роликів"}}]"""

    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip().removeprefix("```json").removesuffix("```").strip()
    return json.loads(text)


def claude_chat(user_message: str, trends_context: str = "") -> str:
    """Claude відповідає на питання користувача з контекстом трендів."""
    system_prompt = """Ти — експерт з TikTok трендів і контент-креатор.
Допомагаєш відеомонтажерам робити вірусні рекламні та UGC-ролики.
Відповідай коротко, практично, з конкретними порадами.
У тебе Є доступ до свіжих даних трендів користувача — вони приходять у
повідомленні нижче, зібрані сьогодні через наш власний Apify-скрейпер.
Використовуй їх напряму для відповіді. НІКОЛИ не кажи, що не маєш доступу
до інтернету чи актуальних даних у реальному часі — доступ уже є, дані
надані в повідомленні."""

    if trends_context:
        prompt = (
            "Ось реальні дані з нашого пулу трендів за сьогодні (зібрані щойно "
            f"через Apify для ніші користувача):\n\n{trends_context}\n\n"
            "Відповідай на основі цих даних. Не кажи, що не маєш доступу до "
            "інтернету — доступ вже є, ось дані.\n\n"
            f"Питання користувача: {user_message}"
        )
    else:
        prompt = (
            f"{user_message}\n\n"
            "(Свіжих даних пулу трендів зараз немає — дай практичну відповідь "
            "загалом, без посилань на конкретні відео.)"
        )

    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=system_prompt,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def claude_music_pick(sounds: list[dict], style_label: str) -> list[dict]:
    """Claude відбирає з топ-звуків ті, що пасують стилю (евристика по назві/автору)."""
    prompt = f"""Ти — музичний редактор для TikTok-роликів.
Ось топ-звуки з трендових відео (за частотою використання):

{json.dumps(sounds, ensure_ascii=False, indent=1)}

Користувач шукає звуки в стилі: {style_label}.
У тебе є ТІЛЬКИ назва треку і автор (без аудіо), тому суди за текстовими метаданими.
Вибери до 5 звуків, що найбільше пасують стилю, і поясни.
Відповідай ТІЛЬКИ валідним JSON-масивом без markdown, формат:
[{{"musicName": "...", "musicAuthor": "...",
"fit": "1 коротке речення чому пасує стилю",
"idea": "1 речення як використати в рекламному/UGC-ролику"}}]
Якщо нічого явно не пасує — поверни []."""

    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip().removeprefix("```json").removesuffix("```").strip()
    return json.loads(text)


# ---------------- Digest ----------------
def build_digest_text(top: list[dict], region: str) -> str:
    today = datetime.now(timezone.utc).strftime("%d.%m")
    lines = [
        f"🔥 <b>Трендовий дайджест {today}</b>",
        f"<i>{region_label(region)} — тренди по регіону (за geo)</i>\n",
    ]
    for i, v in enumerate(top, 1):
        lines.append(
            f"{i}. ▶️ Дивитись: {escape(v.get('url', ''))}\n"
            f"💡 <i>Чому:</i> {escape(v.get('why', ''))}\n"
            f"🎯 <i>Вкрасти:</i> {escape(v.get('steal', ''))}\n"
        )
    return "\n".join(lines)


async def send_digest(context: ContextTypes.DEFAULT_TYPE, chat_id: int | str, prefs: dict):
    """Шле дайджест: наступні BATCH_SIZE непоказаних відео з пулу (+ 'Далі' знизу)."""
    chat_id = int(chat_id)
    niche_key = pool_niche_key(prefs)
    hashtags = resolve_hashtags(prefs)
    region = prefs.get("region") or "global"
    try:
        videos, fell_back = await ensure_pool(chat_id, niche_key, hashtags, region)
        if fell_back:
            await notify_region_fallback(context, chat_id, region)
        seen = await db.get_seen_urls(chat_id, niche_key)
        unseen = [v for v in videos if v.get("url") and v["url"] not in seen]

        if len(unseen) < BATCH_SIZE:
            await context.bot.send_message(chat_id=chat_id, text="🔄 Оновлюю пул трендів…")
            videos, fell_back2 = await ensure_pool(chat_id, niche_key, hashtags, region, force=True)
            if fell_back2 and not fell_back:
                await notify_region_fallback(context, chat_id, region)
            seen = await db.get_seen_urls(chat_id, niche_key)
            unseen = [v for v in videos if v.get("url") and v["url"] not in seen]

        note = ""
        if not unseen:
            # Навіть свіжий пул повністю збігається з уже показаним — краще повтор, ніж тиша.
            unseen = [v for v in videos if v.get("url")]
            note = "\n<i>Нових відео поки немає — показую найсильніше з поточного пулу.</i>"
        if not unseen:
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ Apify нічого не повернув — перевір хештеги або кредити.",
                reply_markup=main_menu_keyboard(),
            )
            return

        batch = unseen[:15]
        top = (await asyncio.to_thread(claude_rank, batch))[:BATCH_SIZE]
        await db.add_seen(chat_id, niche_key, [v.get("url", "") for v in top])
        text = build_digest_text(top, region) + note
    except Exception as e:
        log.exception("Digest failed")
        text = f"⚠️ Дайджест впав: {escape(str(e))}"

    await context.bot.send_message(
        chat_id=chat_id, text=text,
        parse_mode=ParseMode.HTML, disable_web_page_preview=True,
        reply_markup=whats_next_keyboard(niche_key),
    )


# ---------------- Музичний дайджест ----------------
def top_sounds(videos: list[dict], top_n: int = 5) -> list[dict]:
    """Частота musicId серед відео пулу -> топ найчастіших звуків."""
    counter: dict[str, dict] = {}
    for v in videos:
        mid = v.get("musicId")
        if not mid:
            continue
        entry = counter.setdefault(mid, {
            "musicName": v.get("musicName", ""),
            "musicAuthor": v.get("musicAuthor", ""),
            "playUrl": v.get("musicPlayUrl", ""),
            "exampleVideo": v.get("url", ""),
            "count": 0,
        })
        entry["count"] += 1
    return sorted(counter.values(), key=lambda e: -e["count"])[:top_n]


async def send_music_digest(context: ContextTypes.DEFAULT_TYPE, chat_id: int,
                            prefs: dict, style_key: str):
    niche_key = pool_niche_key(prefs)
    try:
        videos, fell_back = await ensure_pool(
            chat_id, niche_key, resolve_hashtags(prefs), prefs["region"]
        )
        if fell_back:
            await notify_region_fallback(context, chat_id, prefs["region"])
        sounds = top_sounds(videos)
        if not sounds:
            await context.bot.send_message(
                chat_id=chat_id,
                text="🤷 У поточному пулі немає музичних метаданих. Спробуй оновити дайджест.",
                reply_markup=whats_next_keyboard(niche_key),
            )
            return

        lines = [f"🎵 <b>Музика в тренді</b> (топ звуків серед {len(videos)} відео пулу)\n"]
        for i, s in enumerate(sounds, 1):
            name = escape(s["musicName"] or "Без назви")
            author = f" — {escape(s['musicAuthor'])}" if s["musicAuthor"] else ""
            lines.append(f"{i}. <b>{name}</b>{author} · у {s['count']} відео")
            if s["exampleVideo"]:
                lines.append(f"   🎬 Приклад відео: {s['exampleVideo']}")
        lines.append("")

        if style_key != "any":
            style_label = MUSIC_STYLES[style_key]
            picks = await asyncio.to_thread(claude_music_pick, sounds, style_label)
            lines.append(f"🎯 <b>Під стиль {style_label}:</b>")
            if picks:
                for p in picks:
                    author = f" — {escape(p.get('musicAuthor', ''))}" if p.get("musicAuthor") else ""
                    lines.append(f"• <b>{escape(p.get('musicName', ''))}</b>{author}")
                    lines.append(f"  💡 {escape(p.get('fit', ''))}")
                    lines.append(f"  🎬 {escape(p.get('idea', ''))}")
            else:
                lines.append("Серед топ-звуків нічого явно не пасує цьому стилю — глянь загальний топ вище.")
            lines.append("")

        lines.append("ℹ️ <i>Підбір стилю — за назвою/автором треку (метадані), не за аудіо-аналізом.</i>")
        await context.bot.send_message(
            chat_id=chat_id, text="\n".join(lines),
            parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            reply_markup=whats_next_keyboard(niche_key),
        )
    except Exception as e:
        log.exception("Music digest failed")
        await context.bot.send_message(
            chat_id=chat_id, text=f"⚠️ Музичний дайджест впав: {e}",
            reply_markup=whats_next_keyboard(niche_key),
        )


# ---- Контекст трендів для Claude-чату (через кеш-пул, без зайвих Apify runs) ----
async def get_trends_context(chat_id: int, prefs: dict) -> str:
    """Гарантує актуальний trend_pool (ensure_pool) і збирає з нього контекст:
    хештеги ніші, топ-5 відео з url, топ звуки — щоб Claude відповідав
    на реальних даних, а не казав, що "немає доступу до інтернету"."""
    niche_key = pool_niche_key(prefs)
    hashtags = resolve_hashtags(prefs)
    region = prefs.get("region") or "global"
    try:
        videos, _ = await ensure_pool(chat_id, niche_key, hashtags, region)
    except Exception as e:
        log.error(f"Failed to get trends context: {e}")
        return ""
    if not videos:
        return ""

    lines = [
        f"Регіон: {region_label(region)}",
        f"Хештеги ніші: {', '.join('#' + h for h in hashtags)}",
        "",
        "Топ-5 відео пулу (url реальні, з сьогоднішнього скрейпу):",
    ]
    for i, v in enumerate(videos[:5], 1):
        lines.append(f"{i}. {v['desc'][:100]} — {v.get('plays', 0)} переглядів — {v.get('url', '')}")

    sounds = top_sounds(videos, top_n=5)
    if sounds:
        lines.append("")
        lines.append("Топ звуків пулу:")
        for i, s in enumerate(sounds, 1):
            author = f" — {s['musicAuthor']}" if s["musicAuthor"] else ""
            lines.append(f"{i}. {s['musicName']}{author} (у {s['count']} відео)")

    return "\n".join(lines)


# ---------------- Handlers ----------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await db.update_user(chat_id)  # реєструємо користувача
    caption = (
        f"👋 Привіт! Твій chat_id: <code>{chat_id}</code>\n\n"
        "Я буду надсилати тобі дайджест трендових TikTok-відео "
        "(тренди по регіону — за geo).\n"
        "Можеш також запитати мене про тренди і контент!"
    )
    try:
        with open(BANNER_PATH, "rb") as banner:
            await update.message.reply_photo(
                photo=banner, caption=caption,
                parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard(),
            )
    except FileNotFoundError:
        await update.message.reply_text(
            caption, parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard()
        )
    await update.message.reply_text(
        "Кнопка «🏠 Меню» завжди внизу екрана 👇",
        reply_markup=menu_reply_keyboard(),
    )


async def cmd_niche(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показує меню вибору ніші."""
    await update.message.reply_text(
        "🎯 Вибери нішу для дайджесту:",
        reply_markup=niche_menu_keyboard(),
    )


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    prefs = await db.get_user(chat_id)
    if await pool_is_fresh(chat_id, prefs):
        await update.message.reply_text("⏳ Секунду, беру з кешу трендів…")
    else:
        await update.message.reply_text("⏳ Тягну тренди, це 1-3 хв…")
    await send_digest(context, chat_id, prefs)


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запускає режим запитань до Claude."""
    chat_id = update.effective_chat.id
    await db.update_user(chat_id, ask_mode=True)
    await update.message.reply_text(
        "💬 Режим запитань активний!\n\n"
        "Напиши своє питання про TikTok тренди, контент, хуки, тощо.\n"
        "Я буду відповідати з контекстом твоєї ніші.\n\n"
        "Напиши /cancel щоб вийти."
    )


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    prefs = await db.get_user(chat_id)

    if prefs["niche_key"] in NICHES:
        niche_data = NICHES[prefs["niche_key"]]
        current_niche = f"{niche_data['emoji']} {prefs['niche_key'].capitalize()}"
    else:
        current_niche = "Не вибрана (за замовчуванням)"
    hashtags = ", ".join(f"#{h}" for h in resolve_hashtags(prefs))

    keyboard = [
        [InlineKeyboardButton("🎯 Змінити нішу", callback_data="niche_menu")],
        [InlineKeyboardButton("🌍 Змінити регіон", callback_data="region_menu")],
        [InlineKeyboardButton("📊 Дайджест зараз", callback_data="digest_now")],
        [InlineKeyboardButton("🎵 Музика в тренді", callback_data="music_digest")],
        [InlineKeyboardButton("💬 Запитати Claude", callback_data="ask_mode")],
    ]
    await update.message.reply_text(
        f"⚙️ <b>Твої налаштування:</b>\n\n"
        f"Ніша: {current_niche}\n"
        f"Хештеги: {hashtags}\n"
        f"Регіон: {region_label(prefs['region'])} <i>(тренди по регіону — за geo)</i>\n"
        f"Chat ID: <code>{chat_id}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вихід з режиму запитань."""
    chat_id = update.effective_chat.id
    await db.update_user(chat_id, ask_mode=False)
    await update.message.reply_text(
        "❌ Режим запитань вимкнений. Що далі?",
        reply_markup=main_menu_keyboard(),
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Звичайні повідомлення: '🏠 Меню', режим запитань, або fallback-меню."""
    chat_id = update.effective_chat.id
    user_message = (update.message.text or "").strip()

    if user_message == MENU_BUTTON_TEXT:
        await update.message.reply_text(
            "🏠 Головне меню:",
            reply_markup=main_menu_keyboard(),
        )
        return

    prefs = await db.get_user(chat_id)

    if not prefs["ask_mode"]:
        await update.message.reply_text(
            "Не зовсім зрозумів 🙂 Ось що можна зробити:",
            reply_markup=main_menu_keyboard(),
        )
        return

    # Режим запитань: контекст трендів з кеш-пулу + відповідь Claude
    trends_context = await get_trends_context(chat_id, prefs)
    await update.message.reply_text("⏳ Думаю…")
    try:
        response = await asyncio.to_thread(claude_chat, user_message, trends_context)
        await update.message.reply_text(
            response,
            reply_markup=whats_next_keyboard(pool_niche_key(prefs)),
        )
    except Exception as e:
        log.exception("Chat failed")
        await update.message.reply_text(f"⚠️ Помилка: {e}")


async def safe_edit_or_send(query, context: ContextTypes.DEFAULT_TYPE, text: str,
                            reply_markup=None, parse_mode=None):
    """Показує нове inline-меню на місці попереднього, щоб у чаті лишалось
    тільки ОДНЕ активне меню за раз:
    1) edit_message_text — для звичайних текстових повідомлень;
    2) edit_message_caption — якщо попереднє повідомлення було фото (/start-банер);
    3) якщо й це неможливо — знімаємо клавіатуру зі старого повідомлення
       (edit_message_reply_markup(None)) і шлемо нове."""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        return
    except BadRequest:
        pass
    try:
        await query.edit_message_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
        return
    except BadRequest:
        pass
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except BadRequest:
        pass
    await context.bot.send_message(
        chat_id=query.message.chat_id, text=text,
        reply_markup=reply_markup, parse_mode=parse_mode,
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє натискання кнопок."""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id if query.message else query.from_user.id
    data = query.data

    if data == "niche_menu":
        await safe_edit_or_send(
            query, context, "🎯 Вибери нішу для дайджесту:",
            reply_markup=niche_menu_keyboard(),
        )

    elif data.startswith("select_niche_"):
        niche_key = data.replace("select_niche_", "")
        if niche_key in NICHES:
            niche_data = NICHES[niche_key]
            context.user_data["ht_sel"] = set()
            await safe_edit_or_send(
                query, context,
                f"{niche_data['emoji']} <b>{niche_key.capitalize()}</b>\n\n"
                "Обери конкретні хештеги (можна декілька), потім натисни «✅ Готово».\n"
                "Якщо нічого не вибрати — візьму всі.",
                parse_mode=ParseMode.HTML,
                reply_markup=hashtag_keyboard(niche_key, set()),
            )

    elif data.startswith("ht_t_"):
        # toggle чекбокса хештега: ht_t_<niche>_<idx>
        payload = data.removeprefix("ht_t_")
        niche_key, _, idx_str = payload.rpartition("_")
        if niche_key in NICHES and idx_str.isdigit():
            idx = int(idx_str)
            selected: set = context.user_data.setdefault("ht_sel", set())
            selected.symmetric_difference_update({idx})
            try:
                await query.edit_message_reply_markup(
                    reply_markup=hashtag_keyboard(niche_key, selected)
                )
            except BadRequest:
                pass  # подвійний клік — розмітка не змінилась

    elif data.startswith("ht_done_"):
        niche_key = data.removeprefix("ht_done_")
        if niche_key in NICHES:
            tags = NICHES[niche_key]["hashtags"]
            selected = context.user_data.pop("ht_sel", set())
            chosen = [tags[i] for i in sorted(selected) if i < len(tags)] or tags
            await db.update_user(chat_id, niche_key=niche_key, hashtags=chosen)
            await db.clear_pools(chat_id, niche_key)  # хештеги змінились — кеш пулу застарів
            emoji = NICHES[niche_key]["emoji"]
            await safe_edit_or_send(
                query, context,
                f"✅ Ніша: {emoji} <b>{niche_key.capitalize()}</b>\n"
                f"Хештеги: {', '.join('#' + t for t in chosen)}\n\n"
                "🌍 Тепер обери регіон трендів (за geo):",
                parse_mode=ParseMode.HTML,
                reply_markup=region_menu_keyboard(),
            )

    elif data == "region_menu":
        await safe_edit_or_send(
            query, context,
            "🌍 Обери регіон — тренди по регіону (за geo, через проксі країни):",
            reply_markup=region_menu_keyboard(),
        )

    elif data.startswith("select_region_"):
        region = data.removeprefix("select_region_")
        if region in REGIONS:
            await db.update_user(chat_id, region=region)
            prefs = await db.get_user(chat_id)
            await safe_edit_or_send(
                query, context, flow_summary_text(prefs),
                reply_markup=digest_only_keyboard(),
            )

    elif data == "digest_now":
        prefs = await db.get_user(chat_id)
        if await pool_is_fresh(chat_id, prefs):
            await safe_edit_or_send(query, context, "⏳ Секунду, беру з кешу трендів…")
        else:
            await safe_edit_or_send(query, context, "⏳ Тягну тренди, це 1-3 хв…")
        await send_digest(context, chat_id, prefs)

    elif data.startswith("next_"):
        # niche_key з callback_data ігноруємо навмисно: якщо це стара кнопка
        # з попереднього дайджесту (юзер тим часом змінив нішу/хештеги),
        # довіряємо ТІЛЬКИ поточному стану users в БД, а не даті кнопки.
        prefs = await db.get_user(chat_id)
        await context.bot.send_message(chat_id=chat_id, text="⏳ Шукаю наступні відео…")
        await send_digest(context, chat_id, prefs)

    elif data == "ask_mode":
        await db.update_user(chat_id, ask_mode=True)
        await safe_edit_or_send(
            query, context,
            "💬 Режим запитань активний!\n\n"
            "Напиши своє питання про TikTok тренди, контент, хуки, тощо.\n"
            "Я буду відповідати з контекстом твоєї ніші.\n\n"
            "Напиши /cancel щоб вийти.",
        )

    elif data == "music_digest":
        await safe_edit_or_send(
            query, context,
            "🎵 <b>Музика в тренді</b>\n\n"
            "Рахую найчастіші звуки серед відео твого пулу трендів.\n"
            "Обери стиль (підбір — за назвою/автором треку, не за аудіо):",
            parse_mode=ParseMode.HTML,
            reply_markup=music_style_keyboard(),
        )

    elif data.startswith("music_style_"):
        style_key = data.removeprefix("music_style_")
        if style_key in MUSIC_STYLES:
            prefs = await db.get_user(chat_id)
            if await pool_is_fresh(chat_id, prefs):
                await safe_edit_or_send(query, context, "⏳ Аналізую звуки з пулу…")
            else:
                await safe_edit_or_send(query, context, "⏳ Спершу тягну тренди (1-3 хв), потім звуки…")
            await send_music_digest(context, chat_id, prefs, style_key)


async def daily_job(context: ContextTypes.DEFAULT_TYPE):
    if CHAT_ID:
        prefs = await db.get_user(int(CHAT_ID))
        await send_digest(context, CHAT_ID, prefs)


async def post_init(app: Application):
    await db.init_db()
    log.info("SQLite ready: %s", db.DB_PATH)


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("niche", cmd_niche))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.job_queue.run_daily(daily_job, time=dtime(hour=DIGEST_HOUR, tzinfo=timezone.utc))
    log.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
