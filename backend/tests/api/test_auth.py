"""
Integration tests for the auth endpoints (POST /register, /login, /refresh,
/logout, GET /me).

Uses a real PostgreSQL test database (clinicflow_test) and httpx AsyncClient.
No mocks — every test exercises the full stack from HTTP to committed DB rows.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token, hash_refresh_token
from app.models.refresh_token import RefreshToken
from app.models.tenant import Tenant
from app.models.user import User

# ── Shared request payloads ───────────────────────────────────────────────────

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


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _register(client: AsyncClient, payload: dict) -> dict:
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _login(
    client: AsyncClient, subdomain: str, email: str, password: str
) -> dict:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"subdomain": subdomain, "email": email, "password": password},
    )
    return resp


# ── 1. Register success ───────────────────────────────────────────────────────

async def test_register_creates_tenant_and_user_in_db(
    client: AsyncClient, seed_db: AsyncSession
) -> None:
    resp = await client.post("/api/v1/auth/register", json=_CLINIC_A)

    assert resp.status_code == 201
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900          # 15 min × 60 s
    assert len(body["access_token"]) > 0
    assert len(body["refresh_token"]) > 0

    # Access token must decode cleanly
    payload = decode_token(body["access_token"], "access")
    assert payload["role"] == "owner"

    # Tenant row exists in DB
    tenant = (
        await seed_db.execute(
            select(Tenant).where(Tenant.subdomain == "sunrise-pt")
        )
    ).scalar_one_or_none()
    assert tenant is not None
    assert tenant.name == "Sunrise Physical Therapy"
    assert tenant.is_active is True
    assert tenant.plan_tier == "starter"

    # Owner user row exists, linked to the correct tenant
    user = (
        await seed_db.execute(
            select(User).where(User.tenant_id == tenant.id)
        )
    ).scalar_one_or_none()
    assert user is not None
    assert user.email == "alice@sunrise.com"
    assert user.role == "owner"
    assert user.full_name == "Alice Owner"
    assert user.is_active is True
    # Password must be stored hashed, not in plaintext
    assert user.password_hash != "correct-horse-A"
    assert user.password_hash.startswith("$2b$")  # bcrypt prefix


# ── 2. Login success ──────────────────────────────────────────────────────────

async def test_login_returns_valid_jwt_pair(client: AsyncClient) -> None:
    await _register(client, _CLINIC_A)

    resp = await _login(client, "sunrise-pt", "alice@sunrise.com", "correct-horse-A")
    assert resp.status_code == 200

    body = resp.json()
    access_payload = decode_token(body["access_token"], "access")
    refresh_payload = decode_token(body["refresh_token"], "refresh")

    assert access_payload["type"] == "access"
    assert access_payload["role"] == "owner"
    assert refresh_payload["type"] == "refresh"
    assert refresh_payload["sub"] == access_payload["sub"]
    assert refresh_payload["tenant_id"] == access_payload["tenant_id"]
    assert "jti" in refresh_payload          # unique per-token ID present


# ── 3. Login wrong password ───────────────────────────────────────────────────

async def test_login_wrong_password_returns_401(client: AsyncClient) -> None:
    await _register(client, _CLINIC_A)

    resp = await _login(client, "sunrise-pt", "alice@sunrise.com", "WRONG-password")
    assert resp.status_code == 401


async def test_login_unknown_subdomain_returns_401(client: AsyncClient) -> None:
    resp = await _login(client, "no-such-clinic", "alice@sunrise.com", "any")
    assert resp.status_code == 401


# ── 4. Refresh ────────────────────────────────────────────────────────────────

async def test_refresh_returns_new_pair_and_revokes_old(
    client: AsyncClient, seed_db: AsyncSession
) -> None:
    tokens = await _register(client, _CLINIC_A)
    old_refresh = tokens["refresh_token"]

    resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": old_refresh}
    )
    assert resp.status_code == 200
    new_tokens = resp.json()

    # New refresh token differs from the old one
    assert new_tokens["refresh_token"] != old_refresh
    # New access token is structurally valid
    payload = decode_token(new_tokens["access_token"], "access")
    assert payload["type"] == "access"

    # Old token must be marked revoked in the DB
    old_hash = hash_refresh_token(old_refresh)
    stored = (
        await seed_db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == old_hash)
        )
    ).scalar_one_or_none()
    assert stored is not None
    assert stored.revoked_at is not None, "old refresh token should be revoked"

    # Replaying the old refresh token must be rejected (token re-use attack)
    replay = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": old_refresh}
    )
    assert replay.status_code == 401


# ── 5. No token on protected endpoint ────────────────────────────────────────

async def test_get_me_without_token_returns_401(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


async def test_get_me_with_valid_token_returns_user(client: AsyncClient) -> None:
    tokens = await _register(client, _CLINIC_A)

    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert resp.status_code == 200
    me = resp.json()
    assert me["email"] == "alice@sunrise.com"
    assert me["role"] == "owner"
    assert "password_hash" not in me


# ── 6. Tenant isolation ───────────────────────────────────────────────────────

async def test_two_tenants_see_only_their_own_user(client: AsyncClient) -> None:
    """
    Application-level isolation: each JWT resolves only to the user
    that belongs to its tenant.
    Note: DB-level RLS policies will be added and tested in a follow-up
    migration; this test covers the JWT/dependency layer.
    """
    tokens_a = await _register(client, _CLINIC_A)
    # Reuse alice@sunrise.com for clinic B — valid because the unique constraint
    # is (tenant_id, email), not email alone.
    tokens_b = await _register(
        client, {**_CLINIC_B, "owner_email": "alice@sunrise.com"}
    )

    me_a = (
        await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {tokens_a['access_token']}"},
        )
    ).json()
    me_b = (
        await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {tokens_b['access_token']}"},
        )
    ).json()

    # Same email, but different users in different tenants
    assert me_a["email"] == me_b["email"] == "alice@sunrise.com"
    assert me_a["id"] != me_b["id"]
    assert me_a["tenant_id"] != me_b["tenant_id"]

    # Clinic-A's password must NOT work against clinic-B's subdomain
    cross = await _login(
        client, "sunset-dental", "alice@sunrise.com", "correct-horse-A"
    )
    assert cross.status_code == 401, (
        "Tenant A credentials must be rejected when used against Tenant B"
    )


async def test_duplicate_subdomain_returns_409(client: AsyncClient) -> None:
    await _register(client, _CLINIC_A)
    resp = await client.post("/api/v1/auth/register", json=_CLINIC_A)
    assert resp.status_code == 409
