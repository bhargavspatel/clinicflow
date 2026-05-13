"""Lazy ArqRedis pool for the API process.

Used by route handlers that need to enqueue Arq tasks (e.g., fill_waitlist_task
on appointment cancellation).  The pool is created on first use and reused for
the process lifetime.
"""
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings

_pool: ArqRedis | None = None


async def get_arq_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _pool
