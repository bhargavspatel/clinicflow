"""Shared helper functions for API integration tests.

These are plain async functions (not fixtures) so any test can call them directly.
Each test gets a fresh DB via the `client` fixture (TRUNCATE tenants CASCADE before
each test), so helpers don't need to worry about name collisions between tests.
"""
from httpx import AsyncClient

_CLINIC_A = {
    "clinic_name": "Sunrise Physical Therapy",
    "subdomain": "sunrise-pt",
    "owner_full_name": "Alice Owner",
    "owner_email": "alice@sunrise.com",
    "password": "correct-horse-A",
}

_CLINIC_B = {
    "clinic_name": "Sunset Dental",
    "subdomain": "sunset-dental",
    "owner_full_name": "Bob Owner",
    "owner_email": "bob@sunset.com",
    "password": "correct-horse-B",
}


async def register_clinic(client: AsyncClient, data: dict) -> dict:
    resp = await client.post("/api/v1/auth/register", json=data)
    assert resp.status_code == 201, resp.text
    return resp.json()


def auth(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def get_me(client: AsyncClient, tokens: dict) -> dict:
    resp = await client.get("/api/v1/auth/me", headers=auth(tokens))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def create_patient(
    client: AsyncClient,
    tokens: dict,
    *,
    full_name: str = "Test Patient",
    phone: str = "+12125551234",
    **kwargs: object,
) -> dict:
    payload = {"full_name": full_name, "phone": phone, **kwargs}
    resp = await client.post("/api/v1/patients", json=payload, headers=auth(tokens))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def create_provider(
    client: AsyncClient,
    tokens: dict,
    user_id: str,
    *,
    specialty: str = "Physical Therapy",
    is_active: bool = True,
) -> dict:
    payload = {"user_id": user_id, "specialty": specialty, "is_active": is_active}
    resp = await client.post("/api/v1/providers", json=payload, headers=auth(tokens))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def create_appointment(
    client: AsyncClient,
    tokens: dict,
    patient_id: str,
    provider_id: str,
    *,
    scheduled_at: str = "2026-06-15T09:00:00Z",
    appointment_type: str = "initial_eval",
    **kwargs: object,
) -> dict:
    payload = {
        "patient_id": patient_id,
        "provider_id": provider_id,
        "scheduled_at": scheduled_at,
        "appointment_type": appointment_type,
        **kwargs,
    }
    resp = await client.post("/api/v1/appointments", json=payload, headers=auth(tokens))
    assert resp.status_code == 201, resp.text
    return resp.json()
