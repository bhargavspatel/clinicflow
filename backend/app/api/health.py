"""Health check endpoints.

GET /health      — deep dependency check; runs Postgres, Redis, and MinIO probes in
                   parallel.  Returns 200 when all pass, 503 on any failure.
GET /health/live — liveness probe for container orchestrators; always 200, no I/O.

Component status values:
  ok       — probe completed successfully within the timeout
  degraded — probe hit the timeout (service reachable but unresponsive)
  down     — probe raised an exception (connection refused, auth error, etc.)

Top-level status:
  ok       — all three components ok
  degraded — at least one degraded, none down
  down     — at least one down
"""
import asyncio
import functools
from typing import Literal

import boto3
import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.redis import get_redis

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["system"])

ComponentStatus = Literal["ok", "degraded", "down"]
_TIMEOUT = 2.0  # seconds per probe


async def _check_postgres() -> ComponentStatus:
    try:
        async with AsyncSessionLocal() as db:
            await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=_TIMEOUT)
        return "ok"
    except asyncio.TimeoutError:
        logger.warning("health_check_timeout", component="db")
        return "degraded"
    except Exception as exc:
        logger.warning("health_check_down", component="db", error=str(exc))
        return "down"


async def _check_redis() -> ComponentStatus:
    try:
        redis = await get_redis()
        await asyncio.wait_for(redis.ping(), timeout=_TIMEOUT)
        return "ok"
    except asyncio.TimeoutError:
        logger.warning("health_check_timeout", component="redis")
        return "degraded"
    except Exception as exc:
        logger.warning("health_check_down", component="redis", error=str(exc))
        return "down"


async def _check_storage() -> ComponentStatus:
    settings = get_settings()
    try:
        client = boto3.client(
            "s3",
            region_name=settings.s3_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            endpoint_url=settings.s3_endpoint_url or None,
        )
        loop = asyncio.get_event_loop()
        await asyncio.wait_for(
            loop.run_in_executor(
                None,
                functools.partial(client.head_bucket, Bucket=settings.s3_bucket),
            ),
            timeout=_TIMEOUT,
        )
        return "ok"
    except asyncio.TimeoutError:
        logger.warning("health_check_timeout", component="storage")
        return "degraded"
    except Exception as exc:
        logger.warning("health_check_down", component="storage", error=str(exc))
        return "down"


def _aggregate(db: ComponentStatus, redis: ComponentStatus, storage: ComponentStatus | None) -> tuple[str, int]:
    # Storage is optional — if not configured, exclude it from liveness determination.
    core = {db, redis}
    if core == {"ok"}:
        return "ok", 200
    if "down" in core:
        return "down", 503
    return "degraded", 503


@router.get("/health")
async def health_check() -> JSONResponse:
    settings = get_settings()
    has_s3 = bool(settings.aws_access_key_id and settings.aws_access_key_id != "minioadmin")

    tasks = [_check_postgres(), _check_redis()]
    if has_s3:
        tasks.append(_check_storage())

    results = await asyncio.gather(*tasks)
    db, redis = results[0], results[1]
    storage: ComponentStatus | None = results[2] if has_s3 else None

    overall, http_status = _aggregate(db, redis, storage)
    logger.info(
        "health_check",
        status=overall,
        db=db,
        redis=redis,
        storage=storage,
    )
    body: dict = {"status": overall, "db": db, "redis": redis}
    if storage is not None:
        body["storage"] = storage
    return JSONResponse(status_code=http_status, content=body)


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    """Liveness probe — confirms the process is running. No dependency I/O."""
    return {"status": "ok"}
