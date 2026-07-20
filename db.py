"""
Персистентне сховище бота (SQLite через aiosqlite).

Таблиці:
  users          — налаштування користувача (ніша, хештеги, регіон, мова,
                   ask_mode, тариф tier + pro_until)
  seen_videos    — вже показані відео, щоб "Далі" не повторювався (останні 200 на юзера)
  trend_pool     — СПІЛЬНИЙ (не персональний) кеш пачки відео з Apify на
                   (niche_key, region); економить Apify-кредити між юзерами
  daily_usage    — лічильник дайджестів на юзера за добу (ліміт free-тарифу)
  payments       — журнал ручних оплат (адмін ставить Pro через /mark_paid)
  apify_run_log  — коли справді робився Apify-запит (для /stats)
"""

import json
import os
from datetime import datetime, timedelta, timezone

import aiosqlite

# УВАГА (Railway): файлова система стирається при кожному redeploy.
# Щоб база переживала деплої — підключи Volume (наприклад, у /data)
# і постав env DB_PATH усередину точки монтування: DB_PATH=/data/bot.db
DB_PATH = os.environ.get("DB_PATH", "bot.db")

SEEN_KEEP_PER_USER = 200

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    chat_id   INTEGER PRIMARY KEY,
    niche_key TEXT,
    hashtags  TEXT,
    region    TEXT NOT NULL DEFAULT 'global',
    ask_mode  INTEGER NOT NULL DEFAULT 0,
    tier      TEXT NOT NULL DEFAULT 'free',
    lang      TEXT,
    pro_until TEXT
);

CREATE TABLE IF NOT EXISTS seen_videos (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id   INTEGER NOT NULL,
    niche_key TEXT,
    video_url TEXT NOT NULL,
    shown_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seen_chat ON seen_videos (chat_id, niche_key);

CREATE TABLE IF NOT EXISTS trend_pool (
    niche_key   TEXT NOT NULL,
    region      TEXT NOT NULL,
    videos_json TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (niche_key, region)
);

CREATE TABLE IF NOT EXISTS daily_usage (
    chat_id      INTEGER NOT NULL,
    date         TEXT NOT NULL,
    digest_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, date)
);

CREATE TABLE IF NOT EXISTS payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    amount      REAL,
    tier        TEXT NOT NULL,
    paid_at     TEXT NOT NULL,
    valid_until TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payments_chat ON payments (chat_id);
CREATE INDEX IF NOT EXISTS idx_payments_valid_until ON payments (valid_until);

CREATE TABLE IF NOT EXISTS apify_run_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    niche_key  TEXT NOT NULL,
    region     TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_apify_run_log_fetched ON apify_run_log (fetched_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


async def _migrate(conn: aiosqlite.Connection):
    """Легкі міграції для баз, створених до появи tier/lang/pro_until і
    спільного (без chat_id) trend_pool. Безпечно на порожній/новій БД —
    PRAGMA table_info на неіснуючій таблиці просто повертає порожній набір."""
    cur = await conn.execute("PRAGMA table_info(users)")
    user_cols = {row[1] for row in await cur.fetchall()}
    if user_cols:
        if "tier" not in user_cols:
            await conn.execute("ALTER TABLE users ADD COLUMN tier TEXT NOT NULL DEFAULT 'free'")
        if "lang" not in user_cols:
            await conn.execute("ALTER TABLE users ADD COLUMN lang TEXT")
        if "pro_until" not in user_cols:
            await conn.execute("ALTER TABLE users ADD COLUMN pro_until TEXT")

    cur = await conn.execute("PRAGMA table_info(trend_pool)")
    pool_cols = {row[1] for row in await cur.fetchall()}
    if "chat_id" in pool_cols:
        # Кеш став спільним (niche_key, region) замість персонального —
        # старі рядки безпечні викинути, це лише кеш, не дані юзера.
        await conn.execute("DROP TABLE trend_pool")
    await conn.commit()


async def init_db():
    async with aiosqlite.connect(DB_PATH) as conn:
        await _migrate(conn)
        await conn.executescript(SCHEMA)
        await conn.commit()


# ---------------- users ----------------
async def get_user(chat_id: int) -> dict:
    """Повертає налаштування користувача з дефолтами, якщо запису ще немає."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,))
        row = await cur.fetchone()
    if row is None:
        return {"chat_id": chat_id, "niche_key": None, "hashtags": None,
                "region": "global", "ask_mode": False, "tier": "free",
                "lang": None, "pro_until": None}
    return {
        "chat_id": row["chat_id"],
        "niche_key": row["niche_key"],
        "hashtags": json.loads(row["hashtags"]) if row["hashtags"] else None,
        "region": row["region"] or "global",
        "ask_mode": bool(row["ask_mode"]),
        "tier": row["tier"] or "free",
        "lang": row["lang"],
        "pro_until": row["pro_until"],
    }


async def update_user(chat_id: int, **fields):
    """Оновлює окремі поля users (niche_key, hashtags, region, ask_mode,
    tier, lang, pro_until)."""
    allowed = {"niche_key", "hashtags", "region", "ask_mode", "tier", "lang", "pro_until"}
    updates = {}
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"unknown user field: {key}")
        if key == "hashtags" and value is not None:
            value = json.dumps(value, ensure_ascii=False)
        if key == "ask_mode":
            value = int(bool(value))
        updates[key] = value
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,))
        if updates:
            set_sql = ", ".join(f"{k} = ?" for k in updates)
            await db.execute(
                f"UPDATE users SET {set_sql} WHERE chat_id = ?",
                (*updates.values(), chat_id),
            )
        await db.commit()


# ---------------- seen_videos ----------------
async def add_seen(chat_id: int, niche_key: str | None, urls: list[str]):
    """Додає показані url і тримає лише останні SEEN_KEEP_PER_USER записів на юзера."""
    if not urls:
        return
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT INTO seen_videos (chat_id, niche_key, video_url, shown_at) VALUES (?, ?, ?, ?)",
            [(chat_id, niche_key, url, now) for url in urls if url],
        )
        await db.execute(
            """DELETE FROM seen_videos WHERE chat_id = ? AND id NOT IN (
                   SELECT id FROM seen_videos WHERE chat_id = ?
                   ORDER BY id DESC LIMIT ?
               )""",
            (chat_id, chat_id, SEEN_KEEP_PER_USER),
        )
        await db.commit()


async def get_seen_urls(chat_id: int, niche_key: str | None) -> set[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT video_url FROM seen_videos WHERE chat_id = ? AND niche_key IS ?",
            (chat_id, niche_key),
        )
        rows = await cur.fetchall()
    return {r[0] for r in rows}


# ---------------- trend_pool (спільний кеш, БЕЗ chat_id у ключі) ----------------
async def clear_pools(niche_key: str):
    """Скидає кеш пулу для ніші (всі регіони) — використовується адміном/вручну."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM trend_pool WHERE niche_key = ?", (niche_key,))
        await db.commit()


async def get_pool(niche_key: str, region: str) -> tuple[list[dict], datetime] | None:
    """Повертає (videos, fetched_at) або None, якщо кешу немає."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT videos_json, fetched_at FROM trend_pool WHERE niche_key = ? AND region = ?",
            (niche_key, region),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    try:
        videos = json.loads(row[0])
        fetched_at = datetime.fromisoformat(row[1])
    except (ValueError, json.JSONDecodeError):
        return None
    return videos, fetched_at


async def save_pool(niche_key: str, region: str, videos: list[dict]):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO trend_pool (niche_key, region, videos_json, fetched_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT (niche_key, region)
               DO UPDATE SET videos_json = excluded.videos_json, fetched_at = excluded.fetched_at""",
            (niche_key, region, json.dumps(videos, ensure_ascii=False), _now()),
        )
        await db.commit()


async def log_apify_run(niche_key: str, region: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO apify_run_log (niche_key, region, fetched_at) VALUES (?, ?, ?)",
            (niche_key, region, _now()),
        )
        await db.commit()


# ---------------- daily_usage (ліміт дайджестів free-тарифу) ----------------
async def get_digest_count_today(chat_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT digest_count FROM daily_usage WHERE chat_id = ? AND date = ?",
            (chat_id, _today()),
        )
        row = await cur.fetchone()
    return row[0] if row else 0


async def increment_digest_count(chat_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO daily_usage (chat_id, date, digest_count) VALUES (?, ?, 1)
               ON CONFLICT (chat_id, date) DO UPDATE SET digest_count = digest_count + 1""",
            (chat_id, _today()),
        )
        await db.commit()


# ---------------- payments / тариф Pro ----------------
async def mark_paid(chat_id: int, tier: str, days: int, amount: float | None) -> str:
    """Ставить users.tier/pro_until і логує запис в payments.
    Повертає valid_until (ISO-дата)."""
    valid_until = (datetime.now(timezone.utc).date() + timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,))
        await db.execute(
            "UPDATE users SET tier = ?, pro_until = ? WHERE chat_id = ?",
            (tier, valid_until, chat_id),
        )
        await db.execute(
            "INSERT INTO payments (chat_id, amount, tier, paid_at, valid_until) VALUES (?, ?, ?, ?, ?)",
            (chat_id, amount, tier, _now(), valid_until),
        )
        await db.commit()
    return valid_until


async def get_expired_pro_chat_ids() -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT chat_id FROM users WHERE tier = 'pro' AND pro_until IS NOT NULL AND pro_until < ?",
            (_today(),),
        )
        rows = await cur.fetchall()
    return [r[0] for r in rows]


async def downgrade_to_free(chat_id: int):
    await update_user(chat_id, tier="free")


# ---------------- адмінська статистика ----------------
async def count_users() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        row = await cur.fetchone()
    return row[0] or 0


async def count_users_by_tier() -> dict[str, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT tier, COUNT(*) FROM users GROUP BY tier")
        rows = await cur.fetchall()
    return {tier: count for tier, count in rows}


async def list_users(offset: int, limit: int) -> tuple[list[dict], int]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT COUNT(*) FROM users")
        total = (await cur.fetchone())[0]
        cur = await db.execute(
            "SELECT chat_id, niche_key, region, tier, pro_until FROM users "
            "ORDER BY chat_id LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = await cur.fetchall()
    return [dict(r) for r in rows], total


async def count_digests_today() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COALESCE(SUM(digest_count), 0) FROM daily_usage WHERE date = ?",
            (_today(),),
        )
        row = await cur.fetchone()
    return row[0] or 0


async def count_digests_last_7_days() -> int:
    since = (datetime.now(timezone.utc).date() - timedelta(days=6)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COALESCE(SUM(digest_count), 0) FROM daily_usage WHERE date >= ?",
            (since,),
        )
        row = await cur.fetchone()
    return row[0] or 0


async def count_apify_runs_today() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM apify_run_log WHERE substr(fetched_at, 1, 10) = ?",
            (_today(),),
        )
        row = await cur.fetchone()
    return row[0] or 0


# ---------------- дохід ----------------
async def revenue_this_month() -> float:
    month_prefix = datetime.now(timezone.utc).strftime("%Y-%m")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE substr(paid_at, 1, 7) = ?",
            (month_prefix,),
        )
        row = await cur.fetchone()
    return row[0] or 0.0


async def count_active_pro() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM users WHERE tier = 'pro' AND pro_until IS NOT NULL AND pro_until >= ?",
            (_today(),),
        )
        row = await cur.fetchone()
    return row[0] or 0


async def forecast_monthly_revenue() -> float:
    """Прогноз: SUM(amount / днів_оплаченого_періоду * 30) для платежів, чий
    valid_until ще не минув — "якщо всі активні Pro продовжать на тих самих умовах"."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT amount, paid_at, valid_until FROM payments "
            "WHERE valid_until >= ? AND amount IS NOT NULL",
            (_today(),),
        )
        rows = await cur.fetchall()
    total = 0.0
    for amount, paid_at, valid_until in rows:
        try:
            paid_date = datetime.fromisoformat(paid_at).date()
            valid_date = datetime.fromisoformat(valid_until).date()
        except ValueError:
            continue
        days = (valid_date - paid_date).days
        if days <= 0:
            continue
        total += (amount / days) * 30
    return total
