"""Application-level Redis client.

A single connection pool is created on first use and reused for the lifetime
of the process.  Inject with Depends(get_redis) in route handlers.
"""
from redis.asyncio import Redis

from app.core.config import get_settings

_pool: Redis | None = None


def _get_pool() -> Redis:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = Redis.from_url(settings.redis_url, decode_responses=False)
    return _pool


async def get_redis() -> Redis:
    return _get_pool()
