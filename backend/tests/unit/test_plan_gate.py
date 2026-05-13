"""Unit test for plan-gate SMS enforcement in send_sms_task.

External calls mocked:
  - twilio_client.messages.create  (sync MagicMock)
  - openai_client                  (AsyncMock — never reached in this path)
  - redis                          (AsyncMock)

Database: real clinicflow_test instance (via client / seed_db fixtures).

Covered scenarios
-----------------
1. send_sms_task returns {skipped: True, reason: sms_limit_reached} when
   sms_usage_count >= sms_usage_limit, and Twilio is never called.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.subscription import Subscription
from app.services.notification_service import enqueue_reminder
from tests.api.helpers import (
    _CLINIC_A,
    create_appointment,
    create_patient,
    create_provider,
    get_me,
    register_clinic,
)

_TEST_DB_URL = "postgresql+asyncpg://clinicflow:clinicflow@postgres:5432/clinicflow_test"


async def test_send_sms_task_skips_when_sms_limit_reached(
    client: AsyncClient,
    seed_db: AsyncSession,
) -> None:
    """send_sms_task returns skipped=True and never calls Twilio when at the limit."""
    tokens   = await register_clinic(client, _CLINIC_A)
    me       = await get_me(client, tokens)
    provider = await create_provider(client, tokens, me["id"])
    patient  = await create_patient(client, tokens)
    appt     = await create_appointment(
        client, tokens, patient["id"], provider["id"],
        scheduled_at="2026-09-01T09:00:00Z",
    )
    appt_id   = uuid.UUID(appt["id"])
    tenant_id = uuid.UUID(me["tenant_id"])

    # Seed subscription already at the limit
    sub = Subscription(
        tenant_id=tenant_id,
        stripe_subscription_id="sub_at_limit_001",
        stripe_price_id="price_starter_test",
        status="active",
        current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
        sms_usage_count=500,
        sms_usage_limit=500,
    )
    seed_db.add(sub)
    await seed_db.commit()

    # Create a 'queued' Notification row via the service
    seed_db.expire_all()
    notif    = await enqueue_reminder(seed_db, appt_id, sequence_number=1)
    notif_id = notif.id

    # Build worker context with a separate engine (matches production Arq setup)
    engine  = create_async_engine(_TEST_DB_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    twilio_client = MagicMock()
    mock_redis    = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()

    from app.workers.notification_worker import send_sms_task
    ctx = {
        "db_factory":    factory,
        "redis":         mock_redis,
        "openai_client": AsyncMock(),
        "twilio_client": twilio_client,
    }

    try:
        result = await send_sms_task(ctx, notif_id)
    finally:
        await engine.dispose()

    assert result.get("skipped") is True, f"Expected skipped=True, got {result}"
    assert result.get("reason") == "sms_limit_reached"
    twilio_client.messages.create.assert_not_called()
