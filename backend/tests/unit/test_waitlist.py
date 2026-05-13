"""Tests for waitlist_service and fill_waitlist_task.

External calls mocked:
  - twilio_client.messages.create  (sync MagicMock)

Database: real clinicflow_test instance (via client / seed_db fixtures).
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.appointment_event import AppointmentEvent
from app.models.notification import Notification
from app.models.waitlist import WaitlistEntry
from app.services import waitlist_service
from tests.api.helpers import (
    _CLINIC_A,
    auth,
    create_appointment,
    create_patient,
    create_provider,
    get_me,
    register_clinic,
)

_TEST_DB_URL = "postgresql+asyncpg://clinicflow:clinicflow@postgres:5432/clinicflow_test"

# A morning slot far in the future (well past the 2-hr lead-time threshold).
_MORNING_SLOT = "2026-08-01T09:00:00Z"   # 09:00 UTC → "morning"
_AFTERNOON_SLOT = "2026-08-01T14:00:00Z" # 14:00 UTC → "afternoon"


# ── add_to_waitlist ───────────────────────────────────────────────────────────

async def test_add_to_waitlist_creates_entry(
    client: AsyncClient,
    seed_db: AsyncSession,
) -> None:
    """add_to_waitlist persists a WaitlistEntry with correct fields."""
    tokens  = await register_clinic(client, _CLINIC_A)
    me      = await get_me(client, tokens)
    patient = await create_patient(client, tokens)

    tenant_id  = uuid.UUID(me["tenant_id"])
    patient_id = uuid.UUID(patient["id"])

    entry = await waitlist_service.add_to_waitlist(
        seed_db,
        tenant_id=tenant_id,
        patient_id=patient_id,
        preferred_times="morning",
        preferred_days=["Monday", "Wednesday"],  # should be lowercased
    )

    assert entry.id is not None
    assert entry.tenant_id == tenant_id
    assert entry.patient_id == patient_id
    assert entry.preferred_times == "morning"
    assert entry.preferred_days == ["monday", "wednesday"]
    assert entry.filled_at is None


# ── find_waitlist_match ───────────────────────────────────────────────────────

async def test_find_no_match_within_2h_window(seed_db: AsyncSession) -> None:
    """Returns None when the appointment is less than 2 hours away."""
    near_appt = SimpleNamespace(
        scheduled_at=datetime.now(timezone.utc) + timedelta(minutes=90),
        tenant_id=uuid.uuid4(),
        provider_id=uuid.uuid4(),
    )
    result = await waitlist_service.find_waitlist_match(seed_db, near_appt)  # type: ignore[arg-type]
    assert result is None


async def test_find_match_respects_time_preference(
    client: AsyncClient,
    seed_db: AsyncSession,
) -> None:
    """'morning' entry matches a 09:00 appointment; 'afternoon' entry does not."""
    tokens  = await register_clinic(client, _CLINIC_A)
    me      = await get_me(client, tokens)
    patient = await create_patient(client, tokens)
    provider = await create_provider(client, tokens, me["id"])

    tenant_id  = uuid.UUID(me["tenant_id"])
    patient_id = uuid.UUID(patient["id"])
    provider_id = uuid.UUID(provider["id"])

    # Seed an afternoon-preference entry — should NOT match a morning appointment
    afternoon_entry = await waitlist_service.add_to_waitlist(
        seed_db, tenant_id=tenant_id, patient_id=patient_id,
        preferred_times="afternoon",
    )

    appt = await create_appointment(
        client, tokens, patient["id"], provider["id"],
        scheduled_at=_MORNING_SLOT, appointment_type="follow_up",
    )
    from app.models.appointment import Appointment
    seed_db.expire_all()
    appt_row = (
        await seed_db.execute(
            select(Appointment).where(Appointment.id == uuid.UUID(appt["id"]))
        )
    ).scalar_one()

    # Afternoon entry must not match a morning slot
    result = await waitlist_service.find_waitlist_match(seed_db, appt_row)
    assert result is None

    # Add a morning entry — should now match
    morning_entry = await waitlist_service.add_to_waitlist(
        seed_db, tenant_id=tenant_id, patient_id=patient_id,
        preferred_times="morning",
    )
    result2 = await waitlist_service.find_waitlist_match(seed_db, appt_row)
    assert result2 is not None
    assert result2.id == morning_entry.id


# ── fill_waitlist_task ────────────────────────────────────────────────────────

async def test_fill_waitlist_task_notifies_and_marks_filled(
    client: AsyncClient,
    seed_db: AsyncSession,
) -> None:
    """fill_waitlist_task sends SMS, marks entry filled, appends waitlist_filled event."""
    tokens   = await register_clinic(client, _CLINIC_A)
    me       = await get_me(client, tokens)
    provider = await create_provider(client, tokens, me["id"])
    patient  = await create_patient(client, tokens)

    appt = await create_appointment(
        client, tokens, patient["id"], provider["id"],
        scheduled_at=_MORNING_SLOT, appointment_type="initial_eval",
    )
    appt_id    = uuid.UUID(appt["id"])
    tenant_id  = uuid.UUID(me["tenant_id"])
    patient_id = uuid.UUID(patient["id"])

    # Cancel via API
    resp = await client.patch(
        f"/api/v1/appointments/{appt_id}",
        json={"status": "cancelled"},
        headers=auth(tokens),
    )
    assert resp.status_code == 200

    # Add patient to waitlist (seed directly — bypasses arq enqueue)
    await waitlist_service.add_to_waitlist(
        seed_db, tenant_id=tenant_id, patient_id=patient_id,
        preferred_times="morning",
    )

    # Build worker ctx with real DB and mocked Twilio
    engine  = create_async_engine(_TEST_DB_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    twilio_client = MagicMock()
    twilio_client.messages.create.return_value = MagicMock(sid="SMwaitlist123")

    from app.workers.waitlist_worker import fill_waitlist_task
    ctx = {"db_factory": factory, "twilio_client": twilio_client}

    try:
        result = await fill_waitlist_task(ctx, appt_id)
    finally:
        await engine.dispose()

    assert result["status"] == "notified", result
    assert "waitlist_entry_id" in result
    assert "notification_id" in result

    # Verify DB state
    seed_db.expire_all()

    entry_id = uuid.UUID(result["waitlist_entry_id"])
    entry = (
        await seed_db.execute(select(WaitlistEntry).where(WaitlistEntry.id == entry_id))
    ).scalar_one()
    assert entry.filled_at is not None, "WaitlistEntry.filled_at should be set"

    notif_id = uuid.UUID(result["notification_id"])
    notif = (
        await seed_db.execute(select(Notification).where(Notification.id == notif_id))
    ).scalar_one()
    assert notif.status == "sent"
    assert notif.twilio_sid == "SMwaitlist123"
    assert "Dr." in notif.body

    events = (
        await seed_db.execute(
            select(AppointmentEvent).where(
                AppointmentEvent.appointment_id == appt_id,
                AppointmentEvent.event_type == "waitlist_filled",
            )
        )
    ).scalars().all()
    assert len(events) == 1
    assert str(entry_id) == events[0].event_metadata["waitlist_entry_id"]


async def test_fill_waitlist_task_idempotent(
    client: AsyncClient,
    seed_db: AsyncSession,
) -> None:
    """Running fill_waitlist_task twice does not send a second notification."""
    tokens   = await register_clinic(client, _CLINIC_A)
    me       = await get_me(client, tokens)
    provider = await create_provider(client, tokens, me["id"])
    patient  = await create_patient(client, tokens)

    appt = await create_appointment(
        client, tokens, patient["id"], provider["id"],
        scheduled_at=_MORNING_SLOT, appointment_type="follow_up",
    )
    appt_id   = uuid.UUID(appt["id"])
    tenant_id = uuid.UUID(me["tenant_id"])
    patient_id = uuid.UUID(patient["id"])

    await client.patch(
        f"/api/v1/appointments/{appt_id}",
        json={"status": "cancelled"},
        headers=auth(tokens),
    )
    await waitlist_service.add_to_waitlist(
        seed_db, tenant_id=tenant_id, patient_id=patient_id,
        preferred_times="morning",
    )

    engine  = create_async_engine(_TEST_DB_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    twilio_client = MagicMock()
    twilio_client.messages.create.return_value = MagicMock(sid="SMidempotent")

    from app.workers.waitlist_worker import fill_waitlist_task
    ctx = {"db_factory": factory, "twilio_client": twilio_client}

    try:
        result1 = await fill_waitlist_task(ctx, appt_id)
        result2 = await fill_waitlist_task(ctx, appt_id)
    finally:
        await engine.dispose()

    assert result1["status"] == "notified"
    assert result2["status"] == "already_filled"

    # Twilio must have been called exactly once
    assert twilio_client.messages.create.call_count == 1
