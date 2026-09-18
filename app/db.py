import asyncio
import logging
from pathlib import Path

import asyncpg

from .config import settings

log = logging.getLogger("siraj")

_pool: asyncpg.Pool | None = None
_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema.sql"


async def get_pool(retries: int = 10, delay: float = 3.0) -> asyncpg.Pool:
    """Returns the connection pool; waits if the DB is not ready yet (compose ordering)."""
    global _pool
    if _pool is not None:
        return _pool
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            _pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=8)
            return _pool
        except (OSError, asyncpg.PostgresError) as exc:
            last = exc
            log.warning("Could not connect to Postgres (%d/%d): %s", attempt, retries, exc)
            await asyncio.sleep(delay)
    raise RuntimeError(f"Could not connect to Postgres: {last}")


async def migrate() -> None:
    """Applies schema.sql (idempotent). Warns instead of crashing if privileges fall short."""
    if not _SCHEMA_PATH.exists():
        return
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            await conn.execute(sql)
        log.info("Schema applied (schema.sql)")
    except asyncpg.PostgresError as exc:
        log.warning("Could not apply schema automatically (%s). "
                    "Run it by hand: psql \"$DATABASE_URL\" -f schema.sql", exc)


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
