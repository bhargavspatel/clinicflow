"""Integration test for scheduling_worker.schedule_reminders.

Uses the real clinicflow_test database.  Redis enqueue_job is mocked so no
live Arq queue is required.

Key test: running schedule_reminders twice for the same appointment enqueues
exactly one notification — the second pass detects the existing 'reminded'
appointment_event and skips.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.notification import Notification
from tests.api.helpers import (
    _CLINIC_A,
    auth,
    create_appointment,
    create_patient,
    create_provider,
    get_me,
    register_clinic,
)

_TEST_DB_URL = (
    "postgresql+asyncpg://clinicflow:clinicflow@postgres:5432/clinicflow_test"
)


def _in_24hr_window() -> str:
    """Return a scheduled_at ISO string whose 24-hr trigger falls ~30 min from now.

    trigger_time = scheduled_at - 24h = now + 30min  →  inside [now, now+1hr)
    """
    dt = datetime.now(timezone.utc) + timedelta(hours=24, minutes=30)
    return dt.isoformat()


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_duplicate_reminder_not_enqueued(
    client: AsyncClient,
    seed_db: AsyncSession,
) -> None:
    """schedule_reminders called twice enqueues exactly one notification.

    First call:  'reminded' event does not exist → Notification row created,
                 send_sms_task enqueued via Redis.
    Second call: 'reminded' event already exists → skipped, no new notification.
    """
    from app.workers.scheduling_worker import schedule_reminders

    # ── Setup: create appointment in the 24-hr trigger window ─────────────────
    tokens   = await register_clinic(client, _CLINIC_A)
    me       = await get_me(client, tokens)
    provider = await create_provider(client, tokens, me["id"])
    patient  = await create_patient(client, tokens)

    appt = await create_appointment(
        client, tokens, patient["id"], provider["id"],
        scheduled_at=_in_24hr_window(),
        appointment_type="initial_eval",
    )
    appt_id = uuid.UUID(appt["id"])

    # Score the appointment so risk_bucket is populated (required by scheduler)
    score_resp = await client.post(
        f"/api/v1/appointments/{appt['id']}/score",
        headers=auth(tokens),
    )
    assert score_resp.status_code == 200
    assert score_resp.json()["risk_bucket"] is not None

    # ── Build worker ctx (real test DB, mocked Redis) ─────────────────────────
    engine  = create_async_engine(_TEST_DB_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    mock_redis = AsyncMock()
    mock_redis.enqueue_job = AsyncMock()

    ctx = {"db_factory": factory, "redis": mock_redis}

    try:
        # ── First run ─────────────────────────────────────────────────────────
        result1 = await schedule_reminders(ctx)

        assert result1["scheduled"] == 1, (
            f"expected 1 scheduled, got {result1}"
        )
        assert result1["skipped"] == 0
        assert mock_redis.enqueue_job.call_count == 1

        # Notification row must exist in DB
        seed_db.expire_all()
        notifs = (
            await seed_db.execute(
                select(Notification).where(Notification.appointment_id == appt_id)
            )
        ).scalars().all()
        assert len(notifs) == 1, "expected exactly 1 Notification row after first run"
        assert notifs[0].status == "queued"

        # ── Second run (duplicate) ─────────────────────────────────────────────
        result2 = await schedule_reminders(ctx)

        assert result2["scheduled"] == 0, (
            f"expected 0 newly scheduled on second run, got {result2}"
        )
        assert result2["skipped"] == 1

        # enqueue_job must NOT have been called again
        assert mock_redis.enqueue_job.call_count == 1, (
            "enqueue_job was called more than once — duplicate was not suppressed"
        )

        # Still exactly 1 Notification row
        seed_db.expire_all()
        notifs2 = (
            await seed_db.execute(
                select(Notification).where(Notification.appointment_id == appt_id)
            )
        ).scalars().all()
        assert len(notifs2) == 1, "a second Notification row was created incorrectly"

    finally:
        await engine.dispose()
