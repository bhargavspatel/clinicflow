"""Integration tests for GET /portal/slots and POST /portal/reschedule.

Uses the real clinicflow_test database.  Portal JWTs are minted directly via
create_portal_token so no Redis / Twilio is required.
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_portal_token
from app.models.appointment import Appointment
from tests.api.helpers import (
    _CLINIC_A,
    auth,
    create_appointment,
    create_patient,
    create_provider,
    get_me,
    register_clinic,
)

# Fixed future dates — well past today (2026-05-10) so slot filtering never
# prunes them as "in the past".
_APPT_DAY   = "2026-06-10"
_SLOT_09_00 = f"{_APPT_DAY}T09:00:00Z"
_SLOT_09_45 = f"{_APPT_DAY}T09:45:00Z"
_SLOT_10_30 = f"{_APPT_DAY}T10:30:00Z"


def _portal_auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _setup(client: AsyncClient) -> tuple[dict, dict, dict, dict]:
    """Register clinic, create provider + patient. Return (tokens, me, provider, patient)."""
    tokens   = await register_clinic(client, _CLINIC_A)
    me       = await get_me(client, tokens)
    provider = await create_provider(client, tokens, me["id"])
    patient  = await create_patient(client, tokens)
    return tokens, me, provider, patient


# ── GET /portal/slots ─────────────────────────────────────────────────────────

async def test_slots_excludes_booked_time(client: AsyncClient) -> None:
    """A slot occupied by an active appointment is absent; adjacent slot is present."""
    tokens, me, provider, patient = await _setup(client)

    # Book the 09:00 slot
    await create_appointment(
        client, tokens, patient["id"], provider["id"],
        scheduled_at=_SLOT_09_00, appointment_type="follow_up",
    )

    portal_jwt = create_portal_token(
        uuid.uuid4(),          # patient_id (arbitrary for a slots query)
        uuid.uuid4(),          # appointment_id (arbitrary)
        uuid.UUID(me["tenant_id"]),
    )

    resp = await client.get(
        "/api/v1/portal/slots",
        params={
            "provider_id": provider["id"],
            "from_date":   _APPT_DAY,
            "days_ahead":  1,
        },
        headers=_portal_auth(portal_jwt),
    )

    assert resp.status_code == 200
    body = resp.json()
    slot_strings = body["slots"]

    # 09:00 is booked — must not appear
    assert not any("09:00:00" in s for s in slot_strings), (
        f"09:00 should be blocked, got {slot_strings}"
    )
    # 09:45 is free — must appear
    assert any("09:45:00" in s for s in slot_strings), (
        f"09:45 should be available, got {slot_strings}"
    )


async def test_slots_requires_portal_jwt(client: AsyncClient) -> None:
    """A staff access token is rejected with 401 (wrong JWT type)."""
    tokens, me, provider, _ = await _setup(client)

    resp = await client.get(
        "/api/v1/portal/slots",
        params={"provider_id": provider["id"]},
        headers=auth(tokens),   # staff access token, not portal
    )
    assert resp.status_code == 401


# ── POST /portal/reschedule ───────────────────────────────────────────────────

async def test_reschedule_success(
    client: AsyncClient,
    seed_db: AsyncSession,
) -> None:
    """Patient reschedules to an open slot — appointment row reflects new time."""
    tokens, me, provider, patient = await _setup(client)

    appt = await create_appointment(
        client, tokens, patient["id"], provider["id"],
        scheduled_at=_SLOT_09_00, appointment_type="initial_eval",
    )
    appt_id    = uuid.UUID(appt["id"])
    patient_id = uuid.UUID(patient["id"])
    tenant_id  = uuid.UUID(me["tenant_id"])

    portal_jwt = create_portal_token(patient_id, appt_id, tenant_id)

    resp = await client.post(
        "/api/v1/portal/reschedule",
        json={"appointment_id": str(appt_id), "new_slot": _SLOT_10_30},
        headers=_portal_auth(portal_jwt),
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "rescheduled"
    assert "10:30:00" in body["scheduled_at"]

    # Verify DB row
    seed_db.expire_all()
    row = (
        await seed_db.execute(select(Appointment).where(Appointment.id == appt_id))
    ).scalar_one()
    assert row.status == "rescheduled"
    assert row.scheduled_at.hour == 10
    assert row.scheduled_at.minute == 30


async def test_reschedule_wrong_appointment_rejected(client: AsyncClient) -> None:
    """A JWT minted for appointment A cannot reschedule appointment B."""
    tokens, me, provider, patient = await _setup(client)

    appt_a = await create_appointment(
        client, tokens, patient["id"], provider["id"],
        scheduled_at=_SLOT_09_00, appointment_type="initial_eval",
    )
    appt_b = await create_appointment(
        client, tokens, patient["id"], provider["id"],
        scheduled_at=_SLOT_10_30, appointment_type="follow_up",
    )

    patient_id = uuid.UUID(patient["id"])
    tenant_id  = uuid.UUID(me["tenant_id"])
    # JWT is for appt_a, but request targets appt_b
    portal_jwt = create_portal_token(patient_id, uuid.UUID(appt_a["id"]), tenant_id)

    resp = await client.post(
        "/api/v1/portal/reschedule",
        json={"appointment_id": appt_b["id"], "new_slot": _SLOT_09_45},
        headers=_portal_auth(portal_jwt),
    )

    assert resp.status_code == 400
    assert "authorise" in resp.json()["detail"].lower()


async def test_reschedule_occupied_slot_rejected(client: AsyncClient) -> None:
    """Rescheduling to a slot occupied by another appointment returns 400."""
    tokens, me, provider, patient = await _setup(client)

    appt_a = await create_appointment(
        client, tokens, patient["id"], provider["id"],
        scheduled_at=_SLOT_09_00, appointment_type="initial_eval",
    )
    # appt_b sits at 10:30 — the target slot
    await create_appointment(
        client, tokens, patient["id"], provider["id"],
        scheduled_at=_SLOT_10_30, appointment_type="follow_up",
    )

    patient_id = uuid.UUID(patient["id"])
    tenant_id  = uuid.UUID(me["tenant_id"])
    portal_jwt = create_portal_token(patient_id, uuid.UUID(appt_a["id"]), tenant_id)

    resp = await client.post(
        "/api/v1/portal/reschedule",
        json={"appointment_id": appt_a["id"], "new_slot": _SLOT_10_30},
        headers=_portal_auth(portal_jwt),
    )

    assert resp.status_code == 400
    assert "not available" in resp.json()["detail"].lower()
