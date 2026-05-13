"""Integration tests for /api/v1/patients CRUD."""
import pytest
from httpx import AsyncClient

from tests.api.helpers import _CLINIC_A, auth, create_patient, register_clinic


async def test_create_patient_returns_201(client: AsyncClient) -> None:
    tokens = await register_clinic(client, _CLINIC_A)
    resp = await client.post(
        "/api/v1/patients",
        json={"full_name": "Alice Smith", "phone": "+12125551234"},
        headers=auth(tokens),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["full_name"] == "Alice Smith"
    assert body["phone"] == "+12125551234"
    assert "id" in body
    assert "tenant_id" in body
    assert "created_at" in body


async def test_get_patient_by_id(client: AsyncClient) -> None:
    tokens = await register_clinic(client, _CLINIC_A)
    created = await create_patient(client, tokens)
    resp = await client.get(f"/api/v1/patients/{created['id']}", headers=auth(tokens))
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]
    assert resp.json()["full_name"] == created["full_name"]


async def test_get_patient_not_found_returns_404(client: AsyncClient) -> None:
    tokens = await register_clinic(client, _CLINIC_A)
    resp = await client.get(
        "/api/v1/patients/00000000-0000-0000-0000-000000000000",
        headers=auth(tokens),
    )
    assert resp.status_code == 404


async def test_update_patient_partial_patch(client: AsyncClient) -> None:
    tokens = await register_clinic(client, _CLINIC_A)
    created = await create_patient(client, tokens, full_name="Old Name", phone="+12125551234")

    resp = await client.patch(
        f"/api/v1/patients/{created['id']}",
        json={"full_name": "New Name"},
        headers=auth(tokens),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["full_name"] == "New Name"
    assert body["phone"] == "+12125551234"  # unchanged field preserved


async def test_list_patients_returns_all_and_total(client: AsyncClient) -> None:
    tokens = await register_clinic(client, _CLINIC_A)
    await create_patient(client, tokens, full_name="Alice Smith", phone="+12125551001")
    await create_patient(client, tokens, full_name="Bob Jones", phone="+12125551002")

    resp = await client.get("/api/v1/patients", headers=auth(tokens))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["page"] == 1
    assert len(body["items"]) == 2


async def test_list_patients_search_by_name(client: AsyncClient) -> None:
    tokens = await register_clinic(client, _CLINIC_A)
    await create_patient(client, tokens, full_name="Alice Smith", phone="+12125551001")
    await create_patient(client, tokens, full_name="Bob Jones", phone="+12125551002")

    resp = await client.get("/api/v1/patients?search=alice", headers=auth(tokens))
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["full_name"] == "Alice Smith"


async def test_list_patients_search_by_phone(client: AsyncClient) -> None:
    tokens = await register_clinic(client, _CLINIC_A)
    await create_patient(client, tokens, full_name="Alice Smith", phone="+12125551001")
    await create_patient(client, tokens, full_name="Bob Jones", phone="+12125559999")

    resp = await client.get("/api/v1/patients?search=9999", headers=auth(tokens))
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["full_name"] == "Bob Jones"


async def test_list_patients_page_size(client: AsyncClient) -> None:
    tokens = await register_clinic(client, _CLINIC_A)
    for i in range(5):
        await create_patient(
            client, tokens, full_name=f"Patient {i}", phone=f"+1212555{i:04d}"
        )

    resp = await client.get("/api/v1/patients?page=1&page_size=2", headers=auth(tokens))
    body = resp.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["page_size"] == 2


async def test_create_patient_invalid_phone_returns_422(client: AsyncClient) -> None:
    tokens = await register_clinic(client, _CLINIC_A)
    resp = await client.post(
        "/api/v1/patients",
        json={"full_name": "Test", "phone": "5551234"},  # not E.164
        headers=auth(tokens),
    )
    assert resp.status_code == 422


async def test_patients_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/patients")
    assert resp.status_code == 401
