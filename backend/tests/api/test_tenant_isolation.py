"""Tenant isolation tests: rows created by tenant A must be invisible to tenant B.

These tests exercise both the JWT/service-level filter (WHERE tenant_id = ?)
and the Postgres RLS policy (USING (tenant_id = current_setting(...)::uuid)).
The clinicflow DB user is a superuser so RLS is bypassed in seed_db fixtures,
but the app-level DB session is NOT a superuser — RLS applies to all app queries.
"""
import pytest
from httpx import AsyncClient

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


# ── Patients ──────────────────────────────────────────────────────────────────

async def test_patient_get_invisible_to_other_tenant(client: AsyncClient) -> None:
    tokens_a = await register_clinic(client, _CLINIC_A)
    tokens_b = await register_clinic(client, _CLINIC_B)

    patient_a = await create_patient(client, tokens_a)

    resp = await client.get(
        f"/api/v1/patients/{patient_a['id']}", headers=auth(tokens_b)
    )
    assert resp.status_code == 404


async def test_patient_list_shows_only_own_tenant(client: AsyncClient) -> None:
    tokens_a = await register_clinic(client, _CLINIC_A)
    tokens_b = await register_clinic(client, _CLINIC_B)

    await create_patient(client, tokens_a, full_name="Alice A", phone="+12125551001")
    await create_patient(client, tokens_a, full_name="Bob A", phone="+12125551002")
    await create_patient(client, tokens_b, full_name="Carol B", phone="+12125551003")

    resp_a = await client.get("/api/v1/patients", headers=auth(tokens_a))
    resp_b = await client.get("/api/v1/patients", headers=auth(tokens_b))

    assert resp_a.json()["total"] == 2
    assert resp_b.json()["total"] == 1
    assert resp_b.json()["items"][0]["full_name"] == "Carol B"


async def test_patient_update_rejected_across_tenants(client: AsyncClient) -> None:
    tokens_a = await register_clinic(client, _CLINIC_A)
    tokens_b = await register_clinic(client, _CLINIC_B)

    patient_a = await create_patient(client, tokens_a)

    # Tenant B tries to PATCH tenant A's patient
    resp = await client.patch(
        f"/api/v1/patients/{patient_a['id']}",
        json={"full_name": "Hijacked"},
        headers=auth(tokens_b),
    )
    assert resp.status_code == 404

    # Verify the name was not changed (tenant A can still read it unchanged)
    unchanged = await client.get(
        f"/api/v1/patients/{patient_a['id']}", headers=auth(tokens_a)
    )
    assert unchanged.json()["full_name"] == patient_a["full_name"]


# ── Providers ─────────────────────────────────────────────────────────────────

async def test_provider_get_invisible_to_other_tenant(client: AsyncClient) -> None:
    tokens_a = await register_clinic(client, _CLINIC_A)
    tokens_b = await register_clinic(client, _CLINIC_B)

    me_a = await get_me(client, tokens_a)
    provider_a = await create_provider(client, tokens_a, me_a["id"])

    resp = await client.get(
        f"/api/v1/providers/{provider_a['id']}", headers=auth(tokens_b)
    )
    assert resp.status_code == 404


async def test_provider_list_shows_only_own_tenant(client: AsyncClient) -> None:
    tokens_a = await register_clinic(client, _CLINIC_A)
    tokens_b = await register_clinic(client, _CLINIC_B)

    me_a = await get_me(client, tokens_a)
    me_b = await get_me(client, tokens_b)
    await create_provider(client, tokens_a, me_a["id"])
    await create_provider(client, tokens_b, me_b["id"])

    assert len((await client.get("/api/v1/providers", headers=auth(tokens_a))).json()) == 1
    assert len((await client.get("/api/v1/providers", headers=auth(tokens_b))).json()) == 1


# ── Appointments ──────────────────────────────────────────────────────────────

async def test_appointment_get_invisible_to_other_tenant(client: AsyncClient) -> None:
    tokens_a = await register_clinic(client, _CLINIC_A)
    tokens_b = await register_clinic(client, _CLINIC_B)

    me_a = await get_me(client, tokens_a)
    provider_a = await create_provider(client, tokens_a, me_a["id"])
    patient_a = await create_patient(client, tokens_a)
    appt_a = await create_appointment(client, tokens_a, patient_a["id"], provider_a["id"])

    resp = await client.get(
        f"/api/v1/appointments/{appt_a['id']}", headers=auth(tokens_b)
    )
    assert resp.status_code == 404


async def test_appointment_list_shows_only_own_tenant(client: AsyncClient) -> None:
    tokens_a = await register_clinic(client, _CLINIC_A)
    tokens_b = await register_clinic(client, _CLINIC_B)

    me_a = await get_me(client, tokens_a)
    provider_a = await create_provider(client, tokens_a, me_a["id"])
    patient_a = await create_patient(client, tokens_a)
    await create_appointment(
        client, tokens_a, patient_a["id"], provider_a["id"],
        scheduled_at="2026-06-15T09:00:00Z",
    )
    await create_appointment(
        client, tokens_a, patient_a["id"], provider_a["id"],
        scheduled_at="2026-06-16T09:00:00Z",
    )

    me_b = await get_me(client, tokens_b)
    provider_b = await create_provider(client, tokens_b, me_b["id"])
    patient_b = await create_patient(client, tokens_b)
    await create_appointment(client, tokens_b, patient_b["id"], provider_b["id"])

    resp_a = await client.get("/api/v1/appointments", headers=auth(tokens_a))
    resp_b = await client.get("/api/v1/appointments", headers=auth(tokens_b))

    assert resp_a.json()["total"] == 2
    assert resp_b.json()["total"] == 1


async def test_cross_tenant_status_update_rejected(client: AsyncClient) -> None:
    tokens_a = await register_clinic(client, _CLINIC_A)
    tokens_b = await register_clinic(client, _CLINIC_B)

    me_a = await get_me(client, tokens_a)
    provider_a = await create_provider(client, tokens_a, me_a["id"])
    patient_a = await create_patient(client, tokens_a)
    appt_a = await create_appointment(client, tokens_a, patient_a["id"], provider_a["id"])

    # Tenant B tries to confirm tenant A's appointment
    resp = await client.patch(
        f"/api/v1/appointments/{appt_a['id']}",
        json={"status": "confirmed"},
        headers=auth(tokens_b),
    )
    assert resp.status_code == 404

    # Verify status unchanged from tenant A's perspective
    still_scheduled = await client.get(
        f"/api/v1/appointments/{appt_a['id']}", headers=auth(tokens_a)
    )
    assert still_scheduled.json()["status"] == "scheduled"
