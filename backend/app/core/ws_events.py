"""Publish WebSocket events to the Redis pub/sub channel for a tenant.

Any process (API handler, Arq worker) can call `broadcast` — the WebSocket
endpoint subscribes to the same channel and forwards messages to connected
clients in real time.
"""
import json

import structlog
from redis.asyncio import Redis

logger = structlog.get_logger(__name__)

_CHANNEL = "clinicflow:ws:{tenant_id}"


async def broadcast(redis: Redis, tenant_id: str, event: str, data: dict) -> None:
    try:
        payload = json.dumps({"event": event, "data": data})
        await redis.publish(_CHANNEL.format(tenant_id=tenant_id), payload)
    except Exception:
        logger.warning("ws_broadcast_failed", event=event, tenant_id=tenant_id, exc_info=True)
