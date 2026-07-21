"""
TikTok Trend Bot — персональний трендовий дайджест у Telegram + AI чат.
Apify (скрейпінг TikTok) -> Claude (фільтр + розбір) -> Telegram.

Команди (юзер):
  /start     — вибір мови (перший раз) + привітання + головне меню
  /niche     — вибрати нішу + конкретні/власні хештеги + регіон (у т.ч. довільний код країни)
  /digest    — дайджест прямо зараз
  /settings  — налаштування
  /ask       — запитати Claude про тренди (контекст з твоєї ніші)
  /cancel    — вийти з режиму запитань / скасувати поточний ввід
  /language  — змінити мову інтерфейсу
  /upgrade   — Free vs Pro, як перейти на Pro
Команди (тільки ADMIN_CHAT_ID):
  /set_tier <chat_id> <free|pro>
  /mark_paid <chat_id> <days> [amount]
  /users, /stats, /revenue

Щодня о DIGEST_HOUR (UTC) шле дайджест автоматично; о 00:05 UTC —
перевірка прострочених Pro-підписок.

Стан (ніша, хештеги, регіон, мова, тариф, показані відео) зберігається в
SQLite (bot.db) і переживає рестарти Railway. trend_pool — СПІЛЬНИЙ кеш
на (niche_key, region) між усіма юзерами (економія Apify-кредитів);
seen_videos лишається персональним, щоб "Далі" не повторював саме юзеру.
"""

import asyncio
import json
import logging
import os
import re
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
from locales import (
    DEFAULT_LANG,
    LANG_ENGLISH_NAME,
    LANGUAGE_PROMPT,
    LANGUAGES,
    music_style_label,
    niche_label,
    region_display,
    t,
)

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
POOL_SIZE = int(os.environ.get("POOL_SIZE", "30"))            # скільки відео тримаємо в кеш-пулі
POOL_TTL_HOURS = int(os.environ.get("POOL_TTL_HOURS", "3"))   # коли пул вважати застарілим
BATCH_SIZE = 5                                                # скільки відео в одному дайджесті

ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")

FREE_DAILY_DIGEST_LIMIT = 1
FREE_MUSIC_TOP_N = 3
PRO_MUSIC_TOP_N = 5
USERS_PAGE_SIZE = 20

APIFY_ACTOR = "clockworks~tiktok-scraper"
BANNER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "welcome.png")

COUNTRY_CODE_RE = re.compile(r"^[A-Z]{2}$")

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ---- Ніші (назви — через locales.niche_label, тут лише хештеги й емодзі) ----
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

# ---- Регіони: preset -> ISO-код для Apify proxy (None = Global, без проксі).
# Довільні коди країн (не з цього списку) теж підтримуються — див. region_country_code().
REGIONS = {
    "global": None,
    "ua": "UA",
    "us": "US",
    "gb": "GB",
    "pl": "PL",
    "de": "DE",
    "br": "BR",
    "id": "ID",
}

MUSIC_STYLE_KEYS = ["energy", "emotional", "rap", "any"]


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


def region_country_code(region: str) -> str | None:
    """ISO-код країни для Apify proxy: preset -> REGIONS[...]; довільний код
    (не в REGIONS) використовується як є, uppercase 2 літери; Global -> None.
    Навмисно НЕ прив'язано до списку з 8 preset-регіонів."""
    if region in REGIONS:
        return REGIONS[region]
    if region and region != "global":
        return region
    return None


def user_lang(prefs: dict) -> str:
    return prefs.get("lang") or DEFAULT_LANG


# ---------------- Клавіатури ----------------
def main_menu_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "btn_niche_menu"), callback_data="niche_menu")],
        [InlineKeyboardButton(t(lang, "btn_region_menu"), callback_data="region_menu")],
        [InlineKeyboardButton(t(lang, "btn_digest_now"), callback_data="digest_now")],
        [InlineKeyboardButton(t(lang, "btn_music_digest"), callback_data="music_digest")],
        [InlineKeyboardButton(t(lang, "btn_ask_mode"), callback_data="ask_mode")],
    ])


def whats_next_keyboard(lang: str, niche_key: str) -> InlineKeyboardMarkup:
    """Рядок 'що далі' під дайджестами і відповідями."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "btn_next"), callback_data=f"next_{niche_key}")],
        [InlineKeyboardButton(t(lang, "btn_niche_short"), callback_data="niche_menu"),
         InlineKeyboardButton(t(lang, "btn_region_short"), callback_data="region_menu")],
        [InlineKeyboardButton(t(lang, "btn_music_digest"), callback_data="music_digest"),
         InlineKeyboardButton(t(lang, "btn_ask_mode"), callback_data="ask_mode")],
    ])


def niche_menu_keyboard(lang: str) -> InlineKeyboardMarkup:
    keyboard = []
    for niche_key, niche_data in NICHES.items():
        keyboard.append([InlineKeyboardButton(
            niche_label(lang, niche_key, niche_data["emoji"]), callback_data=f"select_niche_{niche_key}"
        )])
    return InlineKeyboardMarkup(keyboard)


def region_menu_keyboard(lang: str) -> InlineKeyboardMarkup:
    keys = list(REGIONS)
    rows = []
    for i in range(0, len(keys), 2):
        rows.append([
            InlineKeyboardButton(region_display(lang, k), callback_data=f"select_region_{k}")
            for k in keys[i:i + 2]
        ])
    rows.append([InlineKeyboardButton(t(lang, "region_custom_button"), callback_data="region_custom")])
    return InlineKeyboardMarkup(rows)


def hashtag_keyboard(lang: str, niche_key: str, selected: set[int], custom_count: int = 0) -> InlineKeyboardMarkup:
    tags = NICHES[niche_key]["hashtags"]
    rows = []
    for i, tag in enumerate(tags):
        mark = "✅" if i in selected else "▫️"
        rows.append([InlineKeyboardButton(f"{mark} #{tag}", callback_data=f"ht_t_{niche_key}_{i}")])
    custom_label = (t(lang, "hashtag_custom_button_count", n=custom_count) if custom_count
                    else t(lang, "hashtag_custom_button"))
    rows.append([InlineKeyboardButton(custom_label, callback_data=f"ht_custom_{niche_key}")])
    rows.append([InlineKeyboardButton(t(lang, "btn_done"), callback_data=f"ht_done_{niche_key}")])
    return InlineKeyboardMarkup(rows)


def digest_only_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Єдина кнопка в кінці флоу ніша→хештеги→регіон."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "btn_digest_now"), callback_data="digest_now")],
    ])


def flow_summary_text(lang: str, prefs: dict) -> str:
    """Підсумок поточного вибору юзера після кроку регіону."""
    if prefs.get("niche_key") in NICHES:
        niche_data = NICHES[prefs["niche_key"]]
        niche_txt = niche_label(lang, prefs["niche_key"], niche_data["emoji"])
    else:
        niche_txt = t(lang, "niche_unselected")
    tags_txt = ", ".join(f"#{h}" for h in resolve_hashtags(prefs))
    region_txt = region_display(lang, prefs["region"])
    return t(lang, "flow_summary", niche=niche_txt, tags=tags_txt, region=region_txt)


def music_style_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(music_style_label(lang, key), callback_data=f"music_style_{key}")]
         for key in MUSIC_STYLE_KEYS]
    )


def menu_reply_keyboard(lang: str) -> ReplyKeyboardMarkup:
    """Постійна кнопка меню внизу екрана."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton(t(lang, "menu_button_label"))]],
        resize_keyboard=True,
        is_persistent=True,
    )


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=f"lang_{code}")] for code, label in LANGUAGES]
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
    country = region_country_code(region)
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
    """Apify з регіоном (preset або довільний ISO-код); якщо country/residential
    proxy недоступний на плані — не падаємо, а повторюємо запит без
    proxyCountryCode (Global). Повертає (items, fell_back_to_global)."""
    try:
        return await fetch_tiktoks(hashtags, region), False
    except Exception as e:
        if region == "global" or not region_country_code(region):
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


# ---------------- Пул трендів (СПІЛЬНИЙ кеш на niche_key+region — economить Apify-кредити) ----------------
async def ensure_pool(niche_key: str, hashtags: list[str], region: str,
                      force: bool = False) -> tuple[list[dict], bool]:
    """Повертає (пул відео, чи був фолбек на Global). Пул спільний між усіма
    юзерами з однаковою нішею+регіоном — новий Apify run лише коли кеш
    вичерпано/протух, незалежно від того, хто саме його запросив."""
    if not force:
        cached = await db.get_pool(niche_key, region)
        if cached:
            videos, fetched_at = cached
            age = datetime.now(timezone.utc) - fetched_at
            if videos and age < timedelta(hours=POOL_TTL_HOURS):
                log.info("Pool cache hit (shared): niche=%s region=%s", niche_key, region)
                return videos, False
    items, fell_back = await fetch_tiktoks_safe(hashtags, region)
    videos = prefilter(items, top_n=POOL_SIZE)
    await db.save_pool(niche_key, region, videos)
    await db.log_apify_run(niche_key, region)
    return videos, fell_back


async def notify_region_fallback(context: ContextTypes.DEFAULT_TYPE, chat_id: int, lang: str, region: str):
    await context.bot.send_message(
        chat_id=chat_id,
        text=t(lang, "region_fallback_notice", region=region_display(lang, region)),
    )


async def pool_is_fresh(prefs: dict) -> bool:
    cached = await db.get_pool(pool_niche_key(prefs), prefs["region"])
    if not cached:
        return False
    videos, fetched_at = cached
    return bool(videos) and (datetime.now(timezone.utc) - fetched_at) < timedelta(hours=POOL_TTL_HOURS)


# ---------------- Claude ----------------
# Промпти-інструкції лишаються українською (система "думає" нею для якості),
# але явно наказуємо Claude писати ФІНАЛЬНІ поля відповіді мовою юзера.
def claude_rank(videos: list[dict], lang: str) -> list[dict]:
    """Claude вибирає топ-5 і пояснює, чому віральне і що вкрасти."""
    lang_name = LANG_ENGLISH_NAME.get(lang, "English")
    rank_view = [{k: v for k, v in vid.items() if not k.startswith("music")} for vid in videos]
    prompt = f"""Ти — аналітик віральних TikTok-відео для відеомонтажера,
який робить рекламні та UGC-ролики.

Ось дані про відео (velocity_per_hour = перегляди/годину — головний сигнал):

{json.dumps(rank_view, ensure_ascii=False, indent=1)}

Вибери {BATCH_SIZE} найперспективніших для аналізу і натхнення.
Відповідай ТІЛЬКИ валідним JSON-масивом без markdown, формат:
[{{"url": "...", "cover": "...", "why": "1 речення чому віральне (хук/структура/звук/емоція)",
"steal": "1 речення що конкретно вкрасти для своїх роликів"}}]

ВАЖЛИВО: значення полів "why" і "steal" пиши мовою: {lang_name}
(Respond in {lang_name} language)."""

    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip().removeprefix("```json").removesuffix("```").strip()
    return json.loads(text)


def claude_chat(user_message: str, trends_context: str, lang: str) -> str:
    """Claude відповідає на питання користувача з контекстом трендів."""
    lang_name = LANG_ENGLISH_NAME.get(lang, "English")
    system_prompt = f"""Ти — експерт з TikTok трендів і контент-креатор.
Допомагаєш відеомонтажерам робити вірусні рекламні та UGC-ролики.
Відповідай коротко, практично, з конкретними порадами.
У тебе Є доступ до свіжих даних трендів користувача — вони приходять у
повідомленні нижче, зібрані сьогодні через наш власний Apify-скрейпер.
Використовуй їх напряму для відповіді. НІКОЛИ не кажи, що не маєш доступу
до інтернету чи актуальних даних у реальному часі — доступ уже є, дані
надані в повідомленні.

Respond in {lang_name} language — твоя відповідь юзеру МАЄ бути мовою {lang_name},
незалежно від мови цього system-промпту чи наданих даних."""

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


def claude_music_pick(sounds: list[dict], style_label: str, lang: str) -> list[dict]:
    """Claude відбирає з топ-звуків ті, що пасують стилю (евристика по назві/автору)."""
    lang_name = LANG_ENGLISH_NAME.get(lang, "English")
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
Якщо нічого явно не пасує — поверни [].

ВАЖЛИВО: значення полів "fit" і "idea" пиши мовою: {lang_name}
(Respond in {lang_name} language)."""

    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip().removeprefix("```json").removesuffix("```").strip()
    return json.loads(text)


# ---------------- Digest ----------------
def build_digest_text(lang: str, top: list[dict], region: str) -> str:
    today = datetime.now(timezone.utc).strftime("%d.%m")
    lines = [
        t(lang, "digest_header", date=today),
        t(lang, "digest_region_note", region=region_display(lang, region)),
        "",
    ]
    for i, v in enumerate(top, 1):
        lines.append(t(
            lang, "digest_item", n=i,
            url=escape(v.get("url", "")), why=escape(v.get("why", "")), steal=escape(v.get("steal", "")),
        ))
    return "\n".join(lines)


async def send_digest(context: ContextTypes.DEFAULT_TYPE, chat_id: int | str, prefs: dict):
    """Шле дайджест: наступні BATCH_SIZE непоказаних відео зі спільного пулу
    (+ 'Далі' знизу). Free-тариф: максимум FREE_DAILY_DIGEST_LIMIT на добу —
    це ж обмеження природно блокує і повторні натискання 'Далі' того самого дня."""
    chat_id = int(chat_id)
    lang = user_lang(prefs)
    niche_key = pool_niche_key(prefs)
    hashtags = resolve_hashtags(prefs)
    region = prefs.get("region") or "global"
    tier = prefs.get("tier") or "free"

    if tier != "pro":
        used = await db.get_digest_count_today(chat_id)
        if used >= FREE_DAILY_DIGEST_LIMIT:
            await context.bot.send_message(
                chat_id=chat_id, text=t(lang, "digest_limit_reached"),
                reply_markup=whats_next_keyboard(lang, niche_key),
            )
            return

    try:
        videos, fell_back = await ensure_pool(niche_key, hashtags, region)
        if fell_back:
            await notify_region_fallback(context, chat_id, lang, region)
        seen = await db.get_seen_urls(chat_id, niche_key)
        unseen = [v for v in videos if v.get("url") and v["url"] not in seen]

        if len(unseen) < BATCH_SIZE:
            await context.bot.send_message(chat_id=chat_id, text=t(lang, "digest_pool_refreshing"))
            videos, fell_back2 = await ensure_pool(niche_key, hashtags, region, force=True)
            if fell_back2 and not fell_back:
                await notify_region_fallback(context, chat_id, lang, region)
            seen = await db.get_seen_urls(chat_id, niche_key)
            unseen = [v for v in videos if v.get("url") and v["url"] not in seen]

        note = ""
        if not unseen:
            # Навіть свіжий пул повністю збігається з уже показаним — краще повтор, ніж тиша.
            unseen = [v for v in videos if v.get("url")]
            note = "\n" + t(lang, "digest_no_new_note")
        if not unseen:
            await context.bot.send_message(
                chat_id=chat_id, text=t(lang, "digest_empty"),
                reply_markup=main_menu_keyboard(lang),
            )
            return

        batch = unseen[:15]
        top = (await asyncio.to_thread(claude_rank, batch, lang))[:BATCH_SIZE]
        await db.add_seen(chat_id, niche_key, [v.get("url", "") for v in top])
        await db.increment_digest_count(chat_id)
        text = build_digest_text(lang, top, region) + note
    except Exception as e:
        log.exception("Digest failed")
        text = t(lang, "digest_failed", error=escape(str(e)))

    await context.bot.send_message(
        chat_id=chat_id, text=text,
        parse_mode=ParseMode.HTML, disable_web_page_preview=True,
        reply_markup=whats_next_keyboard(lang, niche_key),
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
    lang = user_lang(prefs)
    niche_key = pool_niche_key(prefs)
    tier = prefs.get("tier") or "free"
    top_n = PRO_MUSIC_TOP_N if tier == "pro" else FREE_MUSIC_TOP_N
    try:
        videos, fell_back = await ensure_pool(niche_key, resolve_hashtags(prefs), prefs["region"])
        if fell_back:
            await notify_region_fallback(context, chat_id, lang, prefs["region"])
        sounds = top_sounds(videos, top_n=top_n)
        if not sounds:
            await context.bot.send_message(
                chat_id=chat_id, text=t(lang, "music_empty"),
                reply_markup=whats_next_keyboard(lang, niche_key),
            )
            return

        lines = [t(lang, "music_header", count=len(videos)), ""]
        for i, s in enumerate(sounds, 1):
            name = escape(s["musicName"] or "—")
            author = f" — {escape(s['musicAuthor'])}" if s["musicAuthor"] else ""
            lines.append(t(lang, "music_sound_line", n=i, name=name, author=author, count=s["count"]))
            if s["exampleVideo"]:
                lines.append("   " + t(lang, "music_example_line", url=s["exampleVideo"]))
        lines.append("")

        if style_key != "any":
            style_txt = music_style_label(lang, style_key)
            picks = await asyncio.to_thread(claude_music_pick, sounds, style_txt, lang)
            lines.append(t(lang, "music_style_header", style=style_txt))
            if picks:
                for p in picks:
                    author = f" — {escape(p.get('musicAuthor', ''))}" if p.get("musicAuthor") else ""
                    lines.append(t(
                        lang, "music_style_pick_line",
                        name=escape(p.get("musicName", "")), author=author,
                        fit=escape(p.get("fit", "")), idea=escape(p.get("idea", "")),
                    ))
            else:
                lines.append(t(lang, "music_style_none_match"))
            lines.append("")

        lines.append(t(lang, "music_disclaimer"))
        await context.bot.send_message(
            chat_id=chat_id, text="\n".join(lines),
            parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            reply_markup=whats_next_keyboard(lang, niche_key),
        )
    except Exception as e:
        log.exception("Music digest failed")
        await context.bot.send_message(
            chat_id=chat_id, text=t(lang, "music_failed", error=escape(str(e))),
            reply_markup=whats_next_keyboard(lang, niche_key),
        )


# ---- Контекст трендів для Claude-чату (через спільний кеш-пул, без зайвих Apify runs) ----
async def get_trends_context(prefs: dict) -> str:
    """Гарантує актуальний trend_pool (ensure_pool) і збирає з нього контекст:
    хештеги ніші, топ-5 відео з url, топ звуки — щоб Claude відповідав
    на реальних даних. Цей текст іде ЛИШЕ у промпт Claude (не юзеру напряму),
    тому лишається українською як внутрішня службова розмітка."""
    niche_key = pool_niche_key(prefs)
    hashtags = resolve_hashtags(prefs)
    region = prefs.get("region") or "global"
    try:
        videos, _ = await ensure_pool(niche_key, hashtags, region)
    except Exception as e:
        log.error(f"Failed to get trends context: {e}")
        return ""
    if not videos:
        return ""

    lines = [
        f"Регіон: {region}",
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


# ---------------- Адмін-хелпери ----------------
def is_admin(chat_id: int | str) -> bool:
    return bool(ADMIN_CHAT_ID) and str(chat_id) == str(ADMIN_CHAT_ID)


async def require_admin(update: Update) -> tuple[bool, str]:
    chat_id = update.effective_chat.id
    prefs = await db.get_user(chat_id)
    lang = user_lang(prefs)
    if not is_admin(chat_id):
        await update.message.reply_text(t(lang, "admin_denied"))
        return False, lang
    return True, lang


def format_users_page(users: list[dict], offset: int, total: int) -> str:
    if not users:
        return "Юзерів немає."
    lines = [f"👥 Юзери {offset + 1}-{offset + len(users)} з {total}:\n"]
    for u in users:
        pro_note = ""
        if u["tier"] == "pro" and u["pro_until"]:
            try:
                days_left = (datetime.fromisoformat(u["pro_until"]).date() - datetime.now(timezone.utc).date()).days
                pro_note = f", {days_left}д Pro"
            except ValueError:
                pass
        lines.append(f"{u['chat_id']} | {u['niche_key'] or '-'} | {u['region']} | {u['tier']}{pro_note}")
    return "\n".join(lines)


def users_page_keyboard(offset: int, total: int) -> InlineKeyboardMarkup | None:
    buttons = []
    if offset > 0:
        buttons.append(InlineKeyboardButton("◀️ Назад", callback_data=f"admin_users_{max(0, offset - USERS_PAGE_SIZE)}"))
    if offset + USERS_PAGE_SIZE < total:
        buttons.append(InlineKeyboardButton("Далі ▶️", callback_data=f"admin_users_{offset + USERS_PAGE_SIZE}"))
    return InlineKeyboardMarkup([buttons]) if buttons else None


# ---------------- Handlers (юзер) ----------------
async def send_start_message(message, context: ContextTypes.DEFAULT_TYPE, chat_id: int, lang: str):
    caption = t(lang, "start_caption", chat_id=chat_id)
    try:
        with open(BANNER_PATH, "rb") as banner:
            await message.reply_photo(
                photo=banner, caption=caption,
                parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard(lang),
            )
    except FileNotFoundError:
        await message.reply_text(caption, parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard(lang))
    await message.reply_text(t(lang, "start_menu_hint"), reply_markup=menu_reply_keyboard(lang))


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    prefs = await db.get_user(chat_id)
    if not prefs.get("lang"):
        await update.message.reply_text(LANGUAGE_PROMPT, reply_markup=language_keyboard())
        return
    await send_start_message(update.message, context, chat_id, prefs["lang"])


async def cmd_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(LANGUAGE_PROMPT, reply_markup=language_keyboard())


async def cmd_niche(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    prefs = await db.get_user(chat_id)
    lang = user_lang(prefs)
    await update.message.reply_text(t(lang, "niche_menu_prompt"), reply_markup=niche_menu_keyboard(lang))


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    prefs = await db.get_user(chat_id)
    lang = user_lang(prefs)
    if await pool_is_fresh(prefs):
        await update.message.reply_text(t(lang, "digest_loading_cache"))
    else:
        await update.message.reply_text(t(lang, "digest_loading_fresh"))
    await send_digest(context, chat_id, prefs)


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    prefs = await db.get_user(chat_id)
    lang = user_lang(prefs)
    await db.update_user(chat_id, ask_mode=True)
    await update.message.reply_text(t(lang, "ask_mode_start"))


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    prefs = await db.get_user(chat_id)
    lang = user_lang(prefs)

    if prefs["niche_key"] in NICHES:
        niche_txt = niche_label(lang, prefs["niche_key"], NICHES[prefs["niche_key"]]["emoji"])
    else:
        niche_txt = t(lang, "niche_unselected")
    tags_txt = ", ".join(f"#{h}" for h in resolve_hashtags(prefs))
    region_txt = region_display(lang, prefs["region"])

    keyboard = [
        [InlineKeyboardButton(t(lang, "btn_change_niche"), callback_data="niche_menu")],
        [InlineKeyboardButton(t(lang, "btn_change_region"), callback_data="region_menu")],
        [InlineKeyboardButton(t(lang, "btn_digest_now"), callback_data="digest_now")],
        [InlineKeyboardButton(t(lang, "btn_music_digest"), callback_data="music_digest")],
        [InlineKeyboardButton(t(lang, "btn_ask_mode"), callback_data="ask_mode")],
    ]
    await update.message.reply_text(
        t(lang, "settings_header", niche=niche_txt, tags=tags_txt, region=region_txt,
          tier=prefs["tier"], chat_id=chat_id),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    prefs = await db.get_user(chat_id)
    lang = user_lang(prefs)
    await db.update_user(chat_id, ask_mode=False)
    for key in ("awaiting", "awaiting_niche", "ht_sel", "ht_custom"):
        context.user_data.pop(key, None)
    await update.message.reply_text(t(lang, "cancel_done"), reply_markup=main_menu_keyboard(lang))


async def cmd_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    prefs = await db.get_user(chat_id)
    lang = user_lang(prefs)
    await update.message.reply_text(
        t(lang, "upgrade_text", admin_username=ADMIN_USERNAME), parse_mode=ParseMode.HTML
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Звичайні повідомлення: кнопка меню, очікуваний текстовий ввід
    (код регіону / власні хештеги), режим запитань, або fallback-меню."""
    chat_id = update.effective_chat.id
    user_message = (update.message.text or "").strip()
    prefs = await db.get_user(chat_id)
    lang = user_lang(prefs)

    if user_message == t(lang, "menu_button_label"):
        context.user_data.pop("awaiting", None)
        await update.message.reply_text(t(lang, "menu_opened"), reply_markup=main_menu_keyboard(lang))
        return

    awaiting = context.user_data.get("awaiting")

    if awaiting == "region_code":
        code = user_message.strip().upper()
        if not COUNTRY_CODE_RE.match(code):
            await update.message.reply_text(t(lang, "region_custom_invalid"))
            return
        context.user_data.pop("awaiting", None)
        if prefs["tier"] != "pro":
            await update.message.reply_text(t(lang, "locked_pro"))
            return
        await db.update_user(chat_id, region=code)
        prefs = await db.get_user(chat_id)
        await update.message.reply_text(flow_summary_text(lang, prefs), reply_markup=digest_only_keyboard(lang))
        return

    if awaiting == "hashtags":
        niche_key = context.user_data.get("awaiting_niche")
        if niche_key not in NICHES:
            context.user_data.pop("awaiting", None)
            return
        if prefs["tier"] != "pro":
            context.user_data.pop("awaiting", None)
            await update.message.reply_text(t(lang, "locked_pro"))
            return
        new_tags = [tag.strip().lstrip("#").lower() for tag in user_message.split(",")]
        new_tags = [tag for tag in new_tags if tag]
        if not new_tags:
            await update.message.reply_text(t(lang, "hashtag_custom_empty"))
            return
        custom: list = context.user_data.setdefault("ht_custom", [])
        for tag in new_tags:
            if tag not in custom:
                custom.append(tag)
        context.user_data.pop("awaiting", None)
        selected = context.user_data.get("ht_sel", set())
        total = len(selected) + len(custom)
        await update.message.reply_text(
            t(lang, "hashtag_custom_added", tags=", ".join(f"#{x}" for x in new_tags), total=total),
            reply_markup=hashtag_keyboard(lang, niche_key, selected, len(custom)),
        )
        return

    if not prefs["ask_mode"]:
        await update.message.reply_text(t(lang, "fallback_menu"), reply_markup=main_menu_keyboard(lang))
        return

    trends_context = await get_trends_context(prefs)
    await update.message.reply_text(t(lang, "ask_thinking"))
    try:
        response = await asyncio.to_thread(claude_chat, user_message, trends_context, lang)
        await update.message.reply_text(
            response, reply_markup=whats_next_keyboard(lang, pool_niche_key(prefs)),
        )
    except Exception as e:
        log.exception("Chat failed")
        await update.message.reply_text(t(lang, "ask_failed", error=escape(str(e))))


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
    prefs = await db.get_user(chat_id)
    lang = user_lang(prefs)

    if data == "niche_menu":
        await safe_edit_or_send(
            query, context, t(lang, "niche_menu_prompt"), reply_markup=niche_menu_keyboard(lang),
        )

    elif data.startswith("select_niche_"):
        niche_key = data.replace("select_niche_", "")
        if niche_key in NICHES:
            niche_data = NICHES[niche_key]
            context.user_data["ht_sel"] = set()
            context.user_data["ht_custom"] = []
            context.user_data.pop("awaiting", None)
            niche_txt = niche_label(lang, niche_key, niche_data["emoji"])
            await safe_edit_or_send(
                query, context, t(lang, "hashtag_prompt", niche=niche_txt),
                reply_markup=hashtag_keyboard(lang, niche_key, set(), 0),
            )

    elif data.startswith("ht_t_"):
        # toggle чекбокса хештега: ht_t_<niche>_<idx>
        payload = data.removeprefix("ht_t_")
        niche_key, _, idx_str = payload.rpartition("_")
        if niche_key in NICHES and idx_str.isdigit():
            idx = int(idx_str)
            selected: set = context.user_data.setdefault("ht_sel", set())
            selected.symmetric_difference_update({idx})
            custom = context.user_data.get("ht_custom", [])
            try:
                await query.edit_message_reply_markup(
                    reply_markup=hashtag_keyboard(lang, niche_key, selected, len(custom))
                )
            except BadRequest:
                pass  # подвійний клік — розмітка не змінилась

    elif data.startswith("ht_custom_"):
        niche_key = data.removeprefix("ht_custom_")
        if niche_key in NICHES:
            if prefs["tier"] != "pro":
                await safe_edit_or_send(
                    query, context, t(lang, "locked_pro"),
                    reply_markup=hashtag_keyboard(
                        lang, niche_key, context.user_data.get("ht_sel", set()),
                        len(context.user_data.get("ht_custom", [])),
                    ),
                )
            else:
                context.user_data["awaiting"] = "hashtags"
                context.user_data["awaiting_niche"] = niche_key
                await safe_edit_or_send(query, context, t(lang, "hashtag_custom_prompt"))

    elif data.startswith("ht_done_"):
        niche_key = data.removeprefix("ht_done_")
        if niche_key in NICHES:
            tags = NICHES[niche_key]["hashtags"]
            selected = context.user_data.pop("ht_sel", set())
            custom = context.user_data.pop("ht_custom", [])
            context.user_data.pop("awaiting", None)
            context.user_data.pop("awaiting_niche", None)
            chosen_preset = [tags[i] for i in sorted(selected) if i < len(tags)]
            chosen = chosen_preset + [c for c in custom if c not in chosen_preset]
            if not chosen:
                chosen = tags
            await db.update_user(chat_id, niche_key=niche_key, hashtags=chosen)
            niche_txt = niche_label(lang, niche_key, NICHES[niche_key]["emoji"])
            await safe_edit_or_send(
                query, context,
                t(lang, "hashtag_done_region_prompt", niche=niche_txt, tags=", ".join(f"#{x}" for x in chosen)),
                reply_markup=region_menu_keyboard(lang),
            )

    elif data == "region_menu":
        await safe_edit_or_send(
            query, context, t(lang, "region_menu_prompt"), reply_markup=region_menu_keyboard(lang),
        )

    elif data == "region_custom":
        if prefs["tier"] != "pro":
            await safe_edit_or_send(query, context, t(lang, "locked_pro"), reply_markup=region_menu_keyboard(lang))
        else:
            context.user_data["awaiting"] = "region_code"
            await safe_edit_or_send(query, context, t(lang, "region_custom_prompt"))

    elif data.startswith("select_region_"):
        region = data.removeprefix("select_region_")
        if region in REGIONS:
            if region != "global" and prefs["tier"] != "pro":
                await safe_edit_or_send(
                    query, context, t(lang, "locked_pro"), reply_markup=region_menu_keyboard(lang)
                )
            else:
                await db.update_user(chat_id, region=region)
                prefs = await db.get_user(chat_id)
                await safe_edit_or_send(
                    query, context, flow_summary_text(lang, prefs), reply_markup=digest_only_keyboard(lang),
                )

    elif data == "digest_now":
        if await pool_is_fresh(prefs):
            await safe_edit_or_send(query, context, t(lang, "digest_loading_cache"))
        else:
            await safe_edit_or_send(query, context, t(lang, "digest_loading_fresh"))
        await send_digest(context, chat_id, prefs)

    elif data.startswith("next_"):
        # niche_key з callback_data ігноруємо навмисно: довіряємо ТІЛЬКИ
        # поточному стану users в БД, а не (можливо застарілій) даті кнопки.
        await context.bot.send_message(chat_id=chat_id, text=t(lang, "next_searching"))
        await send_digest(context, chat_id, prefs)

    elif data == "ask_mode":
        await db.update_user(chat_id, ask_mode=True)
        await safe_edit_or_send(query, context, t(lang, "ask_mode_start"))

    elif data == "music_digest":
        await safe_edit_or_send(
            query, context, t(lang, "music_prompt"), reply_markup=music_style_keyboard(lang),
        )

    elif data.startswith("music_style_"):
        style_key = data.removeprefix("music_style_")
        if style_key in MUSIC_STYLE_KEYS:
            if await pool_is_fresh(prefs):
                await safe_edit_or_send(query, context, t(lang, "digest_loading_cache"))
            else:
                await safe_edit_or_send(query, context, t(lang, "digest_loading_fresh"))
            await send_music_digest(context, chat_id, prefs, style_key)

    elif data.startswith("lang_"):
        code = data.removeprefix("lang_")
        if code in dict(LANGUAGES):
            first_time = not prefs["lang"]
            await db.update_user(chat_id, lang=code)
            if first_time:
                await safe_edit_or_send(query, context, t(code, "language_changed"))
                await send_start_message(query.message, context, chat_id, code)
            else:
                await safe_edit_or_send(
                    query, context, t(code, "language_changed"), reply_markup=main_menu_keyboard(code)
                )

    elif data.startswith("admin_users_"):
        if is_admin(chat_id):
            offset = int(data.removeprefix("admin_users_"))
            users, total = await db.list_users(offset, USERS_PAGE_SIZE)
            await safe_edit_or_send(
                query, context, format_users_page(users, offset, total),
                reply_markup=users_page_keyboard(offset, total),
            )


# ---------------- Handlers (адмін) ----------------
async def cmd_set_tier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, lang = await require_admin(update)
    if not ok:
        return
    args = context.args
    if len(args) != 2 or args[1] not in ("free", "pro"):
        await update.message.reply_text("Використання: /set_tier <chat_id> <free|pro>")
        return
    try:
        target_chat_id = int(args[0])
    except ValueError:
        await update.message.reply_text("chat_id має бути числом")
        return
    await db.update_user(target_chat_id, tier=args[1])
    await update.message.reply_text(f"✅ {target_chat_id} → {args[1]}")


async def cmd_mark_paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, lang = await require_admin(update)
    if not ok:
        return
    args = context.args
    if len(args) not in (2, 3):
        await update.message.reply_text("Використання: /mark_paid <chat_id> <days> [amount]")
        return
    try:
        target_chat_id = int(args[0])
        days = int(args[1])
        amount = float(args[2]) if len(args) == 3 else None
    except ValueError:
        await update.message.reply_text("chat_id і days мають бути числами, amount — числом (опційно)")
        return
    if days <= 0:
        await update.message.reply_text("days має бути > 0")
        return

    valid_until = await db.mark_paid(target_chat_id, "pro", days, amount)
    await update.message.reply_text(f"✅ {target_chat_id} → Pro на {days} днів (до {valid_until})")

    target_prefs = await db.get_user(target_chat_id)
    target_lang = user_lang(target_prefs)
    try:
        await context.bot.send_message(
            chat_id=target_chat_id,
            text=t(target_lang, "mark_paid_user_notify", valid_until=valid_until),
        )
    except Exception:
        log.warning("Could not notify %s about mark_paid", target_chat_id)


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, lang = await require_admin(update)
    if not ok:
        return
    users, total = await db.list_users(0, USERS_PAGE_SIZE)
    await update.message.reply_text(
        format_users_page(users, 0, total), reply_markup=users_page_keyboard(0, total)
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, lang = await require_admin(update)
    if not ok:
        return
    total = await db.count_users()
    by_tier = await db.count_users_by_tier()
    today_digests = await db.count_digests_today()
    week_digests = await db.count_digests_last_7_days()
    apify_today = await db.count_apify_runs_today()
    text = (
        "📊 Статистика\n\n"
        f"Юзерів: {total} (Pro: {by_tier.get('pro', 0)}, Free: {by_tier.get('free', 0)})\n"
        f"Дайджестів сьогодні: {today_digests}\n"
        f"Дайджестів за 7 днів: {week_digests}\n"
        f"Apify-запусків сьогодні: {apify_today}"
    )
    await update.message.reply_text(text)


async def cmd_revenue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, lang = await require_admin(update)
    if not ok:
        return
    month_total = await db.revenue_this_month()
    active_pro = await db.count_active_pro()
    forecast = await db.forecast_monthly_revenue()
    text = (
        "💰 Дохід\n\n"
        f"За поточний місяць: {month_total:.2f}\n"
        f"Активних Pro зараз: {active_pro}\n"
        f"Прогноз/міс (якщо всі активні продовжать): {forecast:.2f}"
    )
    await update.message.reply_text(text)


# ---------------- Фонові задачі ----------------
async def daily_job(context: ContextTypes.DEFAULT_TYPE):
    if CHAT_ID:
        prefs = await db.get_user(int(CHAT_ID))
        await send_digest(context, CHAT_ID, prefs)


async def pro_expiry_job(context: ContextTypes.DEFAULT_TYPE):
    """Раз на день: прострочені Pro -> назад на free + повідомлення юзеру."""
    expired = await db.get_expired_pro_chat_ids()
    for uid in expired:
        await db.downgrade_to_free(uid)
        prefs = await db.get_user(uid)
        lang = user_lang(prefs)
        try:
            await context.bot.send_message(chat_id=uid, text=t(lang, "pro_expired_notify"))
        except Exception:
            log.warning("Could not notify %s about Pro expiry", uid)


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
    app.add_handler(CommandHandler("language", cmd_language))
    app.add_handler(CommandHandler("upgrade", cmd_upgrade))
    app.add_handler(CommandHandler("set_tier", cmd_set_tier))
    app.add_handler(CommandHandler("mark_paid", cmd_mark_paid))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("revenue", cmd_revenue))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.job_queue.run_daily(daily_job, time=dtime(hour=DIGEST_HOUR, tzinfo=timezone.utc))
    app.job_queue.run_daily(pro_expiry_job, time=dtime(hour=0, minute=5, tzinfo=timezone.utc))
    if not ADMIN_CHAT_ID:
        log.warning("ADMIN_CHAT_ID не встановлено — адмінські команди недоступні нікому")
    else:
        log.info("Admin chat_id: %s", ADMIN_CHAT_ID)
    log.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
