"""
TikTok Trend Bot — персональний трендовий дайджест у Telegram + AI чат.
Apify (скрейпінг TikTok) -> Claude (фільтр + розбір) -> Telegram.

Команди:
  /start     — показує твій chat_id (потрібен для CHAT_ID)
  /niche     — вибрати нішу для дайджесту
  /digest    — дайджест прямо зараз
  /settings  — налаштування
  /ask       — запитати Claude про тренди (контекст з твоєї ніші)
Щодня о DIGEST_HOUR (UTC) шле дайджест автоматично.
"""

import os
import json
import logging
from datetime import datetime, timezone, time as dtime

import httpx
import anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters

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

APIFY_ACTOR = "clockworks~tiktok-scraper"
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

# Меню команд (постійні кнопки внизу)
MAIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📋 Вибрати нішу"), KeyboardButton("📊 Дайджест")],
        [KeyboardButton("💬 Запитати Claude"), KeyboardButton("⚙️ Налаштування")],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
)

# Зберігання вибраної ніші та контексту трендів на користувача
user_niches = {}
user_trends_cache = {}  # Кеш останніх трендів для контексту


# ---------------- Apify ----------------
async def fetch_tiktoks(hashtags: list[str]) -> list[dict]:
    """Тягне свіжі відео по хештегах через Apify."""
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
    async with httpx.AsyncClient(timeout=300) as http:
        r = await http.post(url, json=payload)
        r.raise_for_status()
        items = r.json()
    log.info("Apify: отримано %d відео", len(items))
    return items


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
    """Топ-N по velocity, компактні поля для Claude."""
    ranked = sorted(items, key=velocity_score, reverse=True)[:top_n]
    slim = []
    for it in ranked:
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
        })
    return slim


# ---------------- Claude ----------------
def claude_rank(videos: list[dict]) -> list[dict]:
    """Claude вибирає топ-5 і пояснює, чому віральне і що вкрасти."""
    prompt = f"""Ти — аналітик віральних TikTok-відео для відеомонтажера,
який робить рекламні та UGC-ролики.

Ось дані про відео (velocity_per_hour = перегляди/годину — головний сигнал):

{json.dumps(videos, ensure_ascii=False, indent=1)}

Вибери 5 найперспективніших для аналізу і натхнення.
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
Якщо користувач питає про тренди — використовуй контекст його останніх трендів."""

    context_msg = ""
    if trends_context:
        context_msg = f"\n\nКонтекст останніх трендів користувача:\n{trends_context}"

    prompt = f"{user_message}{context_msg}"

    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=system_prompt,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


# ---------------- Digest ----------------
async def build_digest(hashtags: list[str]) -> str:
    items = await fetch_tiktoks(hashtags)
    if not items:
        return "Apify нічого не повернув — перевір хештеги або кредити."
    top = claude_rank(prefilter(items))
    today = datetime.now(timezone.utc).strftime("%d.%m")
    lines = [f"🔥 <b>Трендовий дайджест {today}</b>\n"]
    for i, v in enumerate(top, 1):
        lines.append(
            f"{i}. {v['url']}\n"
            f"💡 <i>Чому:</i> {v['why']}\n"
            f"🎯 <i>Вкрасти:</i> {v['steal']}\n"
        )
    return "\n".join(lines)


async def send_digest(context: ContextTypes.DEFAULT_TYPE, chat_id: str, hashtags: list[str] = None):
    if hashtags is None:
        hashtags = HASHTAGS
    try:
        text = await build_digest(hashtags)
    except Exception as e:
        log.exception("Digest failed")
        text = f"⚠️ Дайджест впав: {e}"
    await context.bot.send_message(
        chat_id=chat_id, text=text,
        parse_mode=ParseMode.HTML, disable_web_page_preview=True,
        reply_markup=MAIN_MENU,
    )


# ---- Функція для отримання контексту трендів ----
async def get_trends_context(hashtags: list[str]) -> str:
    """Отримує останні тренди для контексту Claude чату."""
    try:
        items = await fetch_tiktoks(hashtags)
        if not items:
            return ""
        top = prefilter(items, top_n=5)
        context = "Топ тренди:\n"
        for i, v in enumerate(top, 1):
            context += f"{i}. {v['desc'][:100]} ({v['plays']} views)\n"
        return context
    except Exception as e:
        log.error(f"Failed to get trends context: {e}")
        return ""


# ---------------- Handlers ----------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"👋 Привіт! Твій chat_id: <code>{chat_id}</code>\n\n"
        "Я буду надсилати тобі дайджест трендових TikTok-відео.\n"
        "Можеш також запитати мене про тренди і контент!\n\n"
        "Вибери функцію з меню внизу 👇",
        parse_mode=ParseMode.HTML,
        reply_markup=MAIN_MENU,
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє текстові повідомлення (кнопки меню + режим запитань)."""
    chat_id = update.effective_chat.id
    text = update.message.text
    
    # Обробка кнопок меню
    if text == "📋 Вибрати нішу":
        keyboard = []
        for niche_key, niche_data in NICHES.items():
            emoji = niche_data["emoji"]
            keyboard.append([InlineKeyboardButton(f"{emoji} {niche_key.capitalize()}", callback_data=f"select_niche_{niche_key}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "🎯 Вибери нішу для дайджесту:",
            reply_markup=reply_markup,
        )
    
    elif text == "📊 Дайджест":
        hashtags = user_niches.get(chat_id, HASHTAGS)
        await update.message.reply_text("⏳ Тягну тренди, це 1-3 хв…")
        await send_digest(context, chat_id, hashtags)
    
    elif text == "💬 Запитати Claude":
        context.user_data["ask_mode"] = True
        await update.message.reply_text(
            "💬 Режим запитань активний!\n\n"
            "Напиши своє питання про TikTok тренди, контент, хуки, тощо.\n"
            "Я буду відповідати з контекстом твоєї ніші.\n\n"
            "Напиши /cancel щоб вийти.",
            reply_markup=MAIN_MENU,
        )
    
    elif text == "⚙️ Налаштування":
        current_niche = None
        for niche_key, niche_data in NICHES.items():
            if user_niches.get(chat_id) == niche_data["hashtags"]:
                current_niche = f"{niche_data['emoji']} {niche_key.capitalize()}"
                break
        
        if not current_niche:
            current_niche = "Не вибрана (за замовчуванням)"
        
        keyboard = [
            [InlineKeyboardButton("🎯 Змінити нішу", callback_data="niche_menu")],
            [InlineKeyboardButton("📊 Дайджест зараз", callback_data="digest_now")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"⚙️ <b>Твої налаштування:</b>\n\n"
            f"Поточна ніша: {current_niche}\n"
            f"Chat ID: <code>{chat_id}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )
    
    # Режим запитань до Claude
    elif context.user_data.get("ask_mode", False):
        user_message = text
        
        # Отримуємо контекст трендів
        hashtags = user_niches.get(chat_id, HASHTAGS)
        trends_context = await get_trends_context(hashtags)
        
        # Отримуємо відповідь від Claude
        await update.message.reply_text("⏳ Думаю…")
        try:
            response = claude_chat(user_message, trends_context)
            await update.message.reply_text(response, parse_mode=ParseMode.HTML, reply_markup=MAIN_MENU)
        except Exception as e:
            log.exception("Chat failed")
            await update.message.reply_text(f"⚠️ Помилка: {e}", reply_markup=MAIN_MENU)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вихід з режиму запитань."""
    context.user_data["ask_mode"] = False
    await update.message.reply_text("❌ Режим запитань вимкнений.", reply_markup=MAIN_MENU)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє натискання кнопок."""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.from_user.id
    
    if query.data == "niche_menu":
        keyboard = []
        for niche_key, niche_data in NICHES.items():
            emoji = niche_data["emoji"]
            keyboard.append([InlineKeyboardButton(f"{emoji} {niche_key.capitalize()}", callback_data=f"select_niche_{niche_key}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "🎯 Вибери нішу для дайджесту:",
            reply_markup=reply_markup,
        )
    
    elif query.data.startswith("select_niche_"):
        niche_key = query.data.replace("select_niche_", "")
        if niche_key in NICHES:
            niche_data = NICHES[niche_key]
            user_niches[chat_id] = niche_data["hashtags"]
            
            await query.edit_message_text(
                f"✅ Ніша змінена на: {niche_data['emoji']} <b>{niche_key.capitalize()}</b>\n\n"
                f"Хештеги: {', '.join(niche_data['hashtags'])}\n\n"
                "Тепер дайджест буде по цій ніші!",
                parse_mode=ParseMode.HTML,
            )
    
    elif query.data == "digest_now":
        hashtags = user_niches.get(chat_id, HASHTAGS)
        await query.edit_message_text("⏳ Тягну тренди, це 1-3 хв…")
        await send_digest(context, chat_id, hashtags)


async def daily_job(context: ContextTypes.DEFAULT_TYPE):
    if CHAT_ID:
        hashtags = user_niches.get(int(CHAT_ID), HASHTAGS)
        await send_digest(context, CHAT_ID, hashtags)


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.job_queue.run_daily(daily_job, time=dtime(hour=DIGEST_HOUR, tzinfo=timezone.utc))
    log.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()

