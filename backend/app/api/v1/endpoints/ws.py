"""WebSocket endpoint — authenticated real-time event stream.

Authentication: JWT access token passed as ?token=<jwt> query param (browser
WebSocket API does not support custom headers on the initial handshake).

Each tenant gets its own Redis pub/sub channel: clinicflow:ws:{tenant_id}.
API handlers and Arq workers publish events there; this endpoint subscribes
and forwards every message to the connected client.

Close codes:
  4001 — invalid or expired token (client should redirect to /login)
"""
import asyncio

import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.security import TokenError, decode_token

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(
    ws: WebSocket,
    token: str = Query(...),
) -> None:
    # ── 1. Authenticate before accepting — saves a connection slot on bad tokens ─
    try:
        payload = decode_token(token, "access")
        tenant_id: str = payload["tenant_id"]
    except (TokenError, KeyError):
        await ws.close(code=4001, reason="Invalid token")
        return

    await ws.accept()
    settings = get_settings()

    # ── 2. Dedicated Redis connection — pub/sub requires its own connection ──────
    redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    pubsub = redis.pubsub()
    channel = f"clinicflow:ws:{tenant_id}"
    await pubsub.subscribe(channel)

    logger.info("ws_connected", tenant_id=tenant_id)

    async def _forward() -> None:
        """Push Redis messages to the WebSocket client."""
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    await ws.send_text(message["data"])
                except Exception:
                    return

    async def _receive() -> None:
        """Drain inbound WS frames so the browser ping/keepalive doesn't stall."""
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass

    fwd = asyncio.create_task(_forward())
    rcv = asyncio.create_task(_receive())

    try:
        await asyncio.wait({fwd, rcv}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        fwd.cancel()
        rcv.cancel()
        await pubsub.unsubscribe(channel)
        await redis.aclose()
        logger.info("ws_disconnected", tenant_id=tenant_id)
