"""Integration tests for /api/v1/appointments CRUD, filters, and status transitions."""
import pytest
from httpx import AsyncClient

from tests.api.helpers import (
    _CLINIC_A,
    auth,
    create_appointment,
    create_patient,
    create_provider,
    get_me,
    register_clinic,
)


async def _setup(client: AsyncClient) -> tuple[dict, dict, dict]:
    """Register clinic, create one provider and one patient. Returns (tokens, patient, provider)."""
    tokens = await register_clinic(client, _CLINIC_A)
    me = await get_me(client, tokens)
    provider = await create_provider(client, tokens, me["id"])
    patient = await create_patient(client, tokens)
    return tokens, patient, provider


# ── Create ────────────────────────────────────────────────────────────────────

async def test_create_appointment_returns_201(client: AsyncClient) -> None:
    tokens, patient, provider = await _setup(client)

    resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient["id"],
            "provider_id": provider["id"],
            "scheduled_at": "2026-06-15T09:00:00Z",
            "appointment_type": "initial_eval",
        },
        headers=auth(tokens),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "scheduled"
    assert body["duration_minutes"] == 45  # default
    assert body["appointment_type"] == "initial_eval"
    assert body["patient_id"] == patient["id"]
    assert body["provider_id"] == provider["id"]


async def test_create_appointment_invalid_type_returns_422(client: AsyncClient) -> None:
    tokens, patient, provider = await _setup(client)

    resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient["id"],
            "provider_id": provider["id"],
            "scheduled_at": "2026-06-15T09:00:00Z",
            "appointment_type": "not_a_real_type",
        },
        headers=auth(tokens),
    )
    assert resp.status_code == 422


# ── Read ──────────────────────────────────────────────────────────────────────

async def test_get_appointment_by_id(client: AsyncClient) -> None:
    tokens, patient, provider = await _setup(client)
    appt = await create_appointment(client, tokens, patient["id"], provider["id"])

    resp = await client.get(f"/api/v1/appointments/{appt['id']}", headers=auth(tokens))
    assert resp.status_code == 200
    assert resp.json()["id"] == appt["id"]


async def test_get_appointment_not_found_returns_404(client: AsyncClient) -> None:
    tokens = await register_clinic(client, _CLINIC_A)
    resp = await client.get(
        "/api/v1/appointments/00000000-0000-0000-0000-000000000000",
        headers=auth(tokens),
    )
    assert resp.status_code == 404


# ── Status transitions ────────────────────────────────────────────────────────

async def test_valid_transition_scheduled_to_confirmed(client: AsyncClient) -> None:
    tokens, patient, provider = await _setup(client)
    appt = await create_appointment(client, tokens, patient["id"], provider["id"])

    resp = await client.patch(
        f"/api/v1/appointments/{appt['id']}",
        json={"status": "confirmed"},
        headers=auth(tokens),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"


async def test_valid_transition_chain_to_completed(client: AsyncClient) -> None:
    tokens, patient, provider = await _setup(client)
    appt = await create_appointment(client, tokens, patient["id"], provider["id"])

    # scheduled → confirmed
    r1 = await client.patch(
        f"/api/v1/appointments/{appt['id']}",
        json={"status": "confirmed"},
        headers=auth(tokens),
    )
    assert r1.status_code == 200

    # confirmed → completed
    r2 = await client.patch(
        f"/api/v1/appointments/{appt['id']}",
        json={"status": "completed"},
        headers=auth(tokens),
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "completed"


async def test_invalid_transition_scheduled_to_completed_returns_422(
    client: AsyncClient,
) -> None:
    tokens, patient, provider = await _setup(client)
    appt = await create_appointment(client, tokens, patient["id"], provider["id"])

    # Can't skip confirmed and jump directly to completed
    resp = await client.patch(
        f"/api/v1/appointments/{appt['id']}",
        json={"status": "completed"},
        headers=auth(tokens),
    )
    assert resp.status_code == 422
    assert "scheduled" in resp.json()["detail"]
    assert "completed" in resp.json()["detail"]


async def test_terminal_state_blocks_further_transitions(client: AsyncClient) -> None:
    tokens, patient, provider = await _setup(client)
    appt = await create_appointment(client, tokens, patient["id"], provider["id"])

    # Move to a terminal state
    await client.patch(
        f"/api/v1/appointments/{appt['id']}",
        json={"status": "cancelled"},
        headers=auth(tokens),
    )

    # Any further transition must be rejected
    resp = await client.patch(
        f"/api/v1/appointments/{appt['id']}",
        json={"status": "scheduled"},
        headers=auth(tokens),
    )
    assert resp.status_code == 422


async def test_no_show_is_terminal(client: AsyncClient) -> None:
    tokens, patient, provider = await _setup(client)
    appt = await create_appointment(client, tokens, patient["id"], provider["id"])

    await client.patch(
        f"/api/v1/appointments/{appt['id']}",
        json={"status": "no_show"},
        headers=auth(tokens),
    )
    resp = await client.patch(
        f"/api/v1/appointments/{appt['id']}",
        json={"status": "confirmed"},
        headers=auth(tokens),
    )
    assert resp.status_code == 422


# ── List filters ──────────────────────────────────────────────────────────────

async def test_filter_by_status(client: AsyncClient) -> None:
    tokens, patient, provider = await _setup(client)
    appt1 = await create_appointment(
        client, tokens, patient["id"], provider["id"], scheduled_at="2026-06-15T09:00:00Z"
    )
    await create_appointment(
        client, tokens, patient["id"], provider["id"], scheduled_at="2026-06-16T09:00:00Z"
    )

    # Confirm only the first appointment
    await client.patch(
        f"/api/v1/appointments/{appt1['id']}",
        json={"status": "confirmed"},
        headers=auth(tokens),
    )

    resp = await client.get(
        "/api/v1/appointments?status=confirmed", headers=auth(tokens)
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == appt1["id"]


async def test_filter_by_date_range(client: AsyncClient) -> None:
    tokens, patient, provider = await _setup(client)
    await create_appointment(
        client, tokens, patient["id"], provider["id"], scheduled_at="2026-06-10T09:00:00Z"
    )
    await create_appointment(
        client, tokens, patient["id"], provider["id"], scheduled_at="2026-06-20T09:00:00Z"
    )
    await create_appointment(
        client, tokens, patient["id"], provider["id"], scheduled_at="2026-07-05T09:00:00Z"
    )

    resp = await client.get(
        "/api/v1/appointments",
        params={
            "date_from": "2026-06-15T00:00:00Z",
            "date_to": "2026-06-30T23:59:59Z",
        },
        headers=auth(tokens),
    )
    body = resp.json()
    assert body["total"] == 1
    assert "2026-06-20" in body["items"][0]["scheduled_at"]


async def test_filter_by_provider_id(client: AsyncClient) -> None:
    tokens = await register_clinic(client, _CLINIC_A)
    me = await get_me(client, tokens)
    provider1 = await create_provider(
        client, tokens, me["id"], specialty="Physical Therapy"
    )
    patient = await create_patient(client, tokens)

    await create_appointment(
        client, tokens, patient["id"], provider1["id"], scheduled_at="2026-06-15T09:00:00Z"
    )

    # Filter by provider1 — should see only its appointment
    resp = await client.get(
        f"/api/v1/appointments?provider_id={provider1['id']}", headers=auth(tokens)
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["provider_id"] == provider1["id"]


async def test_list_appointments_pagination(client: AsyncClient) -> None:
    tokens, patient, provider = await _setup(client)
    for day in range(1, 6):
        await create_appointment(
            client,
            tokens,
            patient["id"],
            provider["id"],
            scheduled_at=f"2026-06-{day:02d}T09:00:00Z",
        )

    resp = await client.get(
        "/api/v1/appointments?page=1&page_size=2", headers=auth(tokens)
    )
    body = resp.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2


async def test_appointments_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/appointments")
    assert resp.status_code == 401
