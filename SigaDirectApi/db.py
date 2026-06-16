import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
import aiosqlite
from config import DB_PATH


SCHEMA = """
CREATE TABLE IF NOT EXISTS upstream_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    request       TEXT,
    response_meta TEXT
);
CREATE TABLE IF NOT EXISTS downloads (
    token           TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL,
    type            TEXT NOT NULL,
    status          TEXT NOT NULL,          -- pending | ready | empty | failed
    source_filename TEXT,
    stored_path     TEXT,
    size_bytes      INTEGER,
    created_at      TEXT NOT NULL,
    expires_at      TEXT,
    deleted_at      TEXT,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_downloads_job ON downloads(job_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@asynccontextmanager
async def _db():
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    try:
        # busy_timeout is per-connection; lets writers wait out WAL contention
        # across the multiple uvicorn worker processes instead of erroring.
        await conn.execute("PRAGMA busy_timeout = 5000")
        yield conn
    finally:
        await conn.close()


async def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with _db() as conn:
        await conn.execute("PRAGMA journal_mode = WAL")
        await conn.executescript(SCHEMA)
        await conn.commit()


async def log_upstream(request: dict[str, Any], response_meta: dict[str, Any]) -> None:
    async with _db() as conn:
        await conn.execute(
            "INSERT INTO upstream_log (created_at, request, response_meta) VALUES (?, ?, ?)",
            (_now(), json.dumps(request), json.dumps(response_meta)),
        )
        await conn.commit()


async def add_pending_downloads(job_id: str, items: list[tuple[str, str]]) -> None:
    """items: (token, type) per requested archive type, all inserted as pending."""
    now = _now()
    async with _db() as conn:
        await conn.executemany(
            "INSERT INTO downloads (token, job_id, type, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            [(token, job_id, atype, now) for token, atype in items],
        )
        await conn.commit()


async def mark_ready(
    token: str,
    source_filename: str,
    stored_path: str,
    size_bytes: int,
    expires_at: str,
) -> None:
    async with _db() as conn:
        await conn.execute(
            "UPDATE downloads SET status='ready', source_filename=?, stored_path=?, "
            "size_bytes=?, expires_at=? WHERE token=?",
            (source_filename, stored_path, size_bytes, expires_at, token),
        )
        await conn.commit()


async def mark_empty(token: str) -> None:
    async with _db() as conn:
        await conn.execute("UPDATE downloads SET status='empty' WHERE token=?", (token,))
        await conn.commit()


async def mark_failed(token: str, error: str) -> None:
    async with _db() as conn:
        await conn.execute(
            "UPDATE downloads SET status='failed', error=? WHERE token=?", (error, token)
        )
        await conn.commit()


async def get_job(job_id: str) -> list[aiosqlite.Row]:
    async with _db() as conn:
        cur = await conn.execute(
            "SELECT * FROM downloads WHERE job_id=? ORDER BY type", (job_id,)
        )
        return list(await cur.fetchall())


async def get_download(token: str) -> aiosqlite.Row | None:
    async with _db() as conn:
        cur = await conn.execute("SELECT * FROM downloads WHERE token=?", (token,))
        return await cur.fetchone()


async def due_for_cleanup() -> list[aiosqlite.Row]:
    """Ready, undeleted files whose TTL has passed."""
    async with _db() as conn:
        cur = await conn.execute(
            "SELECT * FROM downloads WHERE status='ready' AND deleted_at IS NULL "
            "AND expires_at IS NOT NULL AND expires_at <= ?",
            (_now(),),
        )
        return list(await cur.fetchall())


async def mark_deleted(token: str) -> None:
    async with _db() as conn:
        await conn.execute(
            "UPDATE downloads SET deleted_at=? WHERE token=?", (_now(), token)
        )
        await conn.commit()
