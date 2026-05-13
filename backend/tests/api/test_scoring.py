"""Integration tests for appointment risk scoring.

Covers:
  - POST /appointments/{id}/score HTTP endpoint
  - risk_score / risk_bucket / last_scored_at written to the appointments row
  - appointment_events 'scored' entry with full metadata
  - Idempotency (second score overwrites, same result)
  - Tenant isolation on the score endpoint
  - score_upcoming_appointments worker function (real DB, no mocks)
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.appointment import Appointment
from app.models.appointment_event import AppointmentEvent
from tests.api.helpers import (
    _CLINIC_A,
    _CLINIC_B,
    auth,
    create_appointment,
    create_patient,
    create_provider,
    get_me,
    register_clinic,
)

# Test DB URL — same constant as in conftest.py
_TEST_DB_URL = "postgresql+asyncpg://clinicflow:clinicflow@postgres:5432/clinicflow_test"

# Appointment scheduled 5 days from now at 11am UTC.
# - 5 days < 14 days  → lead-time rule (+15) does NOT fire
# - 11am              → neither Monday 8–10am nor Friday after 4pm rule fires
# Patient has no prior appointments → +8 first-ever
# appointment_type = initial_eval → +5
# Expected total: 13, bucket: "low"
_EXPECTED_SCORE = 13
_EXPECTED_BUCKET = "low"


def _future_slot(days: int = 5) -> str:
    """Return an ISO-8601 datetime string `days` from now at 11:00 UTC."""
    dt = (datetime.now(timezone.utc) + timedelta(days=days)).replace(
        hour=11, minute=0, second=0, microsecond=0
    )
    return dt.isoformat()


async def _setup(client: AsyncClient) -> tuple[dict, dict]:
    """Register clinic, create provider + patient, return (tokens, appointment)."""
    tokens = await register_clinic(client, _CLINIC_A)
    me = await get_me(client, tokens)
    provider = await create_provider(client, tokens, me["id"])
    patient = await create_patient(client, tokens)
    appt = await create_appointment(
        client,
        tokens,
        patient["id"],
        provider["id"],
        scheduled_at=_future_slot(days=5),
        appointment_type="initial_eval",
    )
    return tokens, appt


# ── Endpoint: basic response shape ───────────────────────────────────────────

async def test_score_endpoint_returns_200_with_risk_fields(client: AsyncClient) -> None:
    tokens, appt = await _setup(client)

    resp = await client.post(
        f"/api/v1/appointments/{appt['id']}/score", headers=auth(tokens)
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == appt["id"]
    assert body["risk_score"] is not None
    assert body["risk_bucket"] in ("low", "medium", "high")
    assert body["last_scored_at"] is not None


async def test_score_endpoint_produces_correct_score_for_new_patient(
    client: AsyncClient,
) -> None:
    """New patient + initial_eval + 5-day lead + 11am → +8 +5 = 13, bucket low."""
    tokens, appt = await _setup(client)

    resp = await client.post(
        f"/api/v1/appointments/{appt['id']}/score", headers=auth(tokens)
    )

    body = resp.json()
    assert body["risk_score"] == _EXPECTED_SCORE
    assert body["risk_bucket"] == _EXPECTED_BUCKET


async def test_score_endpoint_returns_404_for_unknown_appointment(
    client: AsyncClient,
) -> None:
    tokens = await register_clinic(client, _CLINIC_A)

    resp = await client.post(
        "/api/v1/appointments/00000000-0000-0000-0000-000000000000/score",
        headers=auth(tokens),
    )

    assert resp.status_code == 404


async def test_score_endpoint_requires_auth(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/appointments/00000000-0000-0000-0000-000000000000/score"
    )
    assert resp.status_code == 401


# ── DB assertions: appointment row ────────────────────────────────────────────

async def test_score_writes_risk_columns_to_appointment_row(
    client: AsyncClient, seed_db: AsyncSession
) -> None:
    """After scoring, appointment.risk_score / risk_bucket / last_scored_at are set."""
    tokens, appt = await _setup(client)

    await client.post(
        f"/api/v1/appointments/{appt['id']}/score", headers=auth(tokens)
    )

    row = (
        await seed_db.execute(
            select(Appointment).where(Appointment.id == uuid.UUID(appt["id"]))
        )
    ).scalar_one()

    assert row.risk_score == _EXPECTED_SCORE
    assert row.risk_bucket == _EXPECTED_BUCKET
    assert row.last_scored_at is not None


# ── DB assertions: appointment_events ────────────────────────────────────────

async def test_score_appends_scored_event_with_metadata(
    client: AsyncClient, seed_db: AsyncSession
) -> None:
    """Scoring appends an event_type='scored' row with risk_score/bucket/factors."""
    tokens, appt = await _setup(client)

    await client.post(
        f"/api/v1/appointments/{appt['id']}/score", headers=auth(tokens)
    )

    event = (
        await seed_db.execute(
            select(AppointmentEvent).where(
                AppointmentEvent.appointment_id == uuid.UUID(appt["id"]),
                AppointmentEvent.event_type == "scored",
            )
        )
    ).scalar_one_or_none()

    assert event is not None
    assert event.actor_id is None  # system-generated, no human actor

    meta = event.event_metadata
    assert meta is not None
    assert meta["risk_score"] == _EXPECTED_SCORE
    assert meta["bucket"] == _EXPECTED_BUCKET
    assert isinstance(meta["factors"], list)
    assert len(meta["factors"]) >= 1  # at minimum "First-ever" fired
    assert any("First-ever" in f for f in meta["factors"])
    assert any("initial evaluation" in f for f in meta["factors"])


async def test_score_event_is_distinct_from_created_event(
    client: AsyncClient, seed_db: AsyncSession
) -> None:
    """Both 'created' and 'scored' events exist after creating + scoring."""
    tokens, appt = await _setup(client)

    await client.post(
        f"/api/v1/appointments/{appt['id']}/score", headers=auth(tokens)
    )

    events = (
        await seed_db.execute(
            select(AppointmentEvent).where(
                AppointmentEvent.appointment_id == uuid.UUID(appt["id"])
            )
        )
    ).scalars().all()

    event_types = {e.event_type for e in events}
    assert "created" in event_types
    assert "scored" in event_types


# ── Idempotency ───────────────────────────────────────────────────────────────

async def test_score_is_idempotent(
    client: AsyncClient, seed_db: AsyncSession
) -> None:
    """Scoring the same appointment twice produces the same result each time."""
    tokens, appt = await _setup(client)

    r1 = await client.post(
        f"/api/v1/appointments/{appt['id']}/score", headers=auth(tokens)
    )
    r2 = await client.post(
        f"/api/v1/appointments/{appt['id']}/score", headers=auth(tokens)
    )

    assert r1.status_code == r2.status_code == 200
    assert r1.json()["risk_score"] == r2.json()["risk_score"]
    assert r1.json()["risk_bucket"] == r2.json()["risk_bucket"]

    # Two 'scored' events — one per call
    scored_events = (
        await seed_db.execute(
            select(AppointmentEvent).where(
                AppointmentEvent.appointment_id == uuid.UUID(appt["id"]),
                AppointmentEvent.event_type == "scored",
            )
        )
    ).scalars().all()
    assert len(scored_events) == 2


# ── Tenant isolation ──────────────────────────────────────────────────────────

async def test_score_endpoint_rejects_cross_tenant_request(
    client: AsyncClient,
) -> None:
    """Tenant B cannot score tenant A's appointment."""
    tokens_a, appt_a = await _setup(client)
    tokens_b = await register_clinic(client, _CLINIC_B)

    resp = await client.post(
        f"/api/v1/appointments/{appt_a['id']}/score", headers=auth(tokens_b)
    )

    assert resp.status_code == 404


# ── Worker function ───────────────────────────────────────────────────────────

async def test_worker_scores_upcoming_appointments(
    client: AsyncClient, seed_db: AsyncSession
) -> None:
    """score_upcoming_appointments() scores all active appointments in next 7 days."""
    from app.workers.scoring_worker import score_upcoming_appointments

    tokens = await register_clinic(client, _CLINIC_A)
    me = await get_me(client, tokens)
    provider = await create_provider(client, tokens, me["id"])
    patient = await create_patient(client, tokens)

    # Two appointments inside the 7-day window
    appt1 = await create_appointment(
        client, tokens, patient["id"], provider["id"],
        scheduled_at=_future_slot(days=2),
        appointment_type="initial_eval",
    )
    appt2 = await create_appointment(
        client, tokens, patient["id"], provider["id"],
        scheduled_at=_future_slot(days=4),
        appointment_type="follow_up",
    )

    # Both should start unscored
    assert appt1["risk_score"] is None
    assert appt2["risk_score"] is None

    # Build a worker ctx wired to the test DB
    engine = create_async_engine(_TEST_DB_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        result = await score_upcoming_appointments({"db_factory": factory})
    finally:
        await engine.dispose()

    # Worker return value
    assert result["total"] >= 2
    assert result["scored"] >= 2
    assert result["failed"] == 0
    assert result["duration_ms"] >= 0

    # Verify DB rows were updated — use expunge so seed_db re-reads from DB
    seed_db.expire_all()

    for appt_id in (appt1["id"], appt2["id"]):
        row = (
            await seed_db.execute(
                select(Appointment).where(Appointment.id == uuid.UUID(appt_id))
            )
        ).scalar_one()
        assert row.risk_score is not None, f"appointment {appt_id} not scored"
        assert row.risk_bucket is not None
        assert row.last_scored_at is not None


async def test_worker_skips_cancelled_appointments(
    client: AsyncClient, seed_db: AsyncSession
) -> None:
    """Cancelled appointments are outside _ACTIVE_STATUSES and must not be scored."""
    from app.workers.scoring_worker import score_upcoming_appointments

    tokens = await register_clinic(client, _CLINIC_A)
    me = await get_me(client, tokens)
    provider = await create_provider(client, tokens, me["id"])
    patient = await create_patient(client, tokens)

    appt = await create_appointment(
        client, tokens, patient["id"], provider["id"],
        scheduled_at=_future_slot(days=2),
        appointment_type="initial_eval",
    )

    # Cancel the appointment before the worker runs
    await client.patch(
        f"/api/v1/appointments/{appt['id']}",
        json={"status": "cancelled"},
        headers=auth(tokens),
    )

    engine = create_async_engine(_TEST_DB_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        result = await score_upcoming_appointments({"db_factory": factory})
    finally:
        await engine.dispose()

    # The cancelled appointment must not appear in the scored count
    seed_db.expire_all()
    row = (
        await seed_db.execute(
            select(Appointment).where(Appointment.id == uuid.UUID(appt["id"]))
        )
    ).scalar_one()

    assert row.risk_score is None
    assert row.risk_bucket is None


async def test_worker_returns_correct_summary_for_empty_window(
    client: AsyncClient,
) -> None:
    """No upcoming appointments → scored=0, failed=0, total=0."""
    from app.workers.scoring_worker import score_upcoming_appointments

    # Register a clinic but create no appointments
    await register_clinic(client, _CLINIC_A)

    engine = create_async_engine(_TEST_DB_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        result = await score_upcoming_appointments({"db_factory": factory})
    finally:
        await engine.dispose()

    assert result["total"] == 0
    assert result["scored"] == 0
    assert result["failed"] == 0
