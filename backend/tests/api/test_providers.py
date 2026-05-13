"""Integration tests for /api/v1/providers CRUD."""
import pytest
from httpx import AsyncClient

from tests.api.helpers import _CLINIC_A, auth, create_provider, get_me, register_clinic


async def test_create_provider_returns_201(client: AsyncClient) -> None:
    tokens = await register_clinic(client, _CLINIC_A)
    me = await get_me(client, tokens)

    resp = await client.post(
        "/api/v1/providers",
        json={"user_id": me["id"], "specialty": "Physical Therapy"},
        headers=auth(tokens),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["specialty"] == "Physical Therapy"
    assert body["is_active"] is True
    assert body["user_id"] == me["id"]
    assert "id" in body


async def test_create_provider_duplicate_user_returns_409(client: AsyncClient) -> None:
    tokens = await register_clinic(client, _CLINIC_A)
    me = await get_me(client, tokens)
    await create_provider(client, tokens, me["id"])

    resp = await client.post(
        "/api/v1/providers",
        json={"user_id": me["id"], "specialty": "Cardiology"},
        headers=auth(tokens),
    )
    assert resp.status_code == 409


async def test_list_providers_returns_all(client: AsyncClient) -> None:
    tokens = await register_clinic(client, _CLINIC_A)
    me = await get_me(client, tokens)
    await create_provider(client, tokens, me["id"])

    resp = await client.get("/api/v1/providers", headers=auth(tokens))
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_get_provider_by_id(client: AsyncClient) -> None:
    tokens = await register_clinic(client, _CLINIC_A)
    me = await get_me(client, tokens)
    provider = await create_provider(client, tokens, me["id"])

    resp = await client.get(f"/api/v1/providers/{provider['id']}", headers=auth(tokens))
    assert resp.status_code == 200
    assert resp.json()["id"] == provider["id"]


async def test_get_provider_not_found_returns_404(client: AsyncClient) -> None:
    tokens = await register_clinic(client, _CLINIC_A)
    resp = await client.get(
        "/api/v1/providers/00000000-0000-0000-0000-000000000000",
        headers=auth(tokens),
    )
    assert resp.status_code == 404


async def test_update_provider_specialty(client: AsyncClient) -> None:
    tokens = await register_clinic(client, _CLINIC_A)
    me = await get_me(client, tokens)
    provider = await create_provider(client, tokens, me["id"])

    resp = await client.patch(
        f"/api/v1/providers/{provider['id']}",
        json={"specialty": "Sports Medicine"},
        headers=auth(tokens),
    )
    assert resp.status_code == 200
    assert resp.json()["specialty"] == "Sports Medicine"
    assert resp.json()["is_active"] is True  # unchanged


async def test_list_providers_filter_by_active(client: AsyncClient) -> None:
    tokens = await register_clinic(client, _CLINIC_A)
    me = await get_me(client, tokens)
    provider = await create_provider(client, tokens, me["id"])

    # Deactivate the provider
    await client.patch(
        f"/api/v1/providers/{provider['id']}",
        json={"is_active": False},
        headers=auth(tokens),
    )

    active = await client.get("/api/v1/providers?is_active=true", headers=auth(tokens))
    inactive = await client.get("/api/v1/providers?is_active=false", headers=auth(tokens))

    assert active.json() == []
    assert len(inactive.json()) == 1


async def test_providers_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/providers")
    assert resp.status_code == 401
