"""
Персистентне сховище бота (SQLite через aiosqlite).

Таблиці:
  users       — налаштування користувача (ніша, хештеги, регіон, ask_mode)
  seen_videos — вже показані відео, щоб "Далі" не повторювався (останні 200 на юзера)
  trend_pool  — кеш пачки відео з Apify, щоб "Далі" не запускав новий run щоразу
"""

import json
import os
from datetime import datetime, timezone

import aiosqlite

DB_PATH = os.environ.get("DB_PATH", "bot.db")

SEEN_KEEP_PER_USER = 200

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    chat_id   INTEGER PRIMARY KEY,
    niche_key TEXT,
    hashtags  TEXT,
    region    TEXT NOT NULL DEFAULT 'global',
    ask_mode  INTEGER NOT NULL DEFAULT 0
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
    chat_id     INTEGER NOT NULL,
    niche_key   TEXT NOT NULL,
    region      TEXT NOT NULL,
    videos_json TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (chat_id, niche_key, region)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


# ---------------- users ----------------
async def get_user(chat_id: int) -> dict:
    """Повертає налаштування користувача з дефолтами, якщо запису ще немає."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,))
        row = await cur.fetchone()
    if row is None:
        return {"chat_id": chat_id, "niche_key": None, "hashtags": None,
                "region": "global", "ask_mode": False}
    return {
        "chat_id": row["chat_id"],
        "niche_key": row["niche_key"],
        "hashtags": json.loads(row["hashtags"]) if row["hashtags"] else None,
        "region": row["region"] or "global",
        "ask_mode": bool(row["ask_mode"]),
    }


async def update_user(chat_id: int, **fields):
    """Оновлює окремі поля users (niche_key, hashtags, region, ask_mode)."""
    allowed = {"niche_key", "hashtags", "region", "ask_mode"}
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


# ---------------- trend_pool ----------------
async def clear_pools(chat_id: int, niche_key: str):
    """Скидає кеш пулу для ніші (всі регіони) — після зміни хештегів."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM trend_pool WHERE chat_id = ? AND niche_key = ?",
            (chat_id, niche_key),
        )
        await db.commit()



async def get_pool(chat_id: int, niche_key: str, region: str) -> tuple[list[dict], datetime] | None:
    """Повертає (videos, fetched_at) або None, якщо кешу немає."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT videos_json, fetched_at FROM trend_pool WHERE chat_id = ? AND niche_key = ? AND region = ?",
            (chat_id, niche_key, region),
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


async def save_pool(chat_id: int, niche_key: str, region: str, videos: list[dict]):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO trend_pool (chat_id, niche_key, region, videos_json, fetched_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (chat_id, niche_key, region)
               DO UPDATE SET videos_json = excluded.videos_json, fetched_at = excluded.fetched_at""",
            (chat_id, niche_key, region, json.dumps(videos, ensure_ascii=False), _now()),
        )
        await db.commit()
