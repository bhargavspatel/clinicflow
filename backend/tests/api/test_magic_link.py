"""Integration tests for the magic link auth flow.

External calls mocked:
  - app.api.v1.endpoints.auth.TwilioClient  (constructor patch)
  - app.core.redis.get_redis                 (FastAPI dependency override)

Database: real clinicflow_test instance (via `client` fixture).

Covered scenarios
-----------------
1. generate  — POST /auth/magic-link/send stores a token in Redis and sends SMS
2. verify    — GET  /auth/magic-link/verify returns a portal JWT and deletes the token
3. expired   — GET  /auth/magic-link/verify with unknown/expired token → 401
"""
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.core.redis import get_redis
from app.main import app
from tests.api.helpers import (
    _CLINIC_A,
    auth,
    create_appointment,
    create_patient,
    create_provider,
    get_me,
    register_clinic,
)

_SCHEDULED_AT = "2026-09-15T10:00:00Z"
_TWILIO_PATCH  = "app.api.v1.endpoints.auth.TwilioClient"


def _make_redis(*, stored: bytes | None = None) -> AsyncMock:
    """Return a minimal Redis mock.

    stored: value returned by redis.get() — None simulates a cache miss / expired token.
    """
    r = AsyncMock()
    r.get    = AsyncMock(return_value=stored)
    r.set    = AsyncMock()
    r.delete = AsyncMock()
    return r


async def _setup(client: AsyncClient) -> tuple[dict, dict, dict, dict, dict]:
    """Register clinic, create provider + patient + appointment."""
    tokens   = await register_clinic(client, _CLINIC_A)
    me       = await get_me(client, tokens)
    provider = await create_provider(client, tokens, me["id"])
    patient  = await create_patient(client, tokens)
    appt     = await create_appointment(
        client, tokens, patient["id"], provider["id"],
        scheduled_at=_SCHEDULED_AT,
    )
    return tokens, me, provider, patient, appt


# ── 1. Generate ───────────────────────────────────────────────────────────────

async def test_magic_link_generate_stores_token_and_sends_sms(
    client: AsyncClient,
) -> None:
    """POST /auth/magic-link/send returns 204, stores a token in Redis, and dispatches SMS."""
    tokens, me, _, patient, appt = await _setup(client)

    mock_redis = _make_redis()

    async def _fake_redis() -> AsyncMock:
        return mock_redis

    app.dependency_overrides[get_redis] = _fake_redis

    try:
        with patch(_TWILIO_PATCH) as mock_twilio_cls:
            mock_twilio_cls.return_value.messages.create.return_value = MagicMock(sid="SM_gen")
            resp = await client.post(
                "/api/v1/auth/magic-link/send",
                json={"appointment_id": appt["id"]},
                headers=auth(tokens),
            )
    finally:
        app.dependency_overrides.pop(get_redis, None)

    assert resp.status_code == 204, resp.text

    # Redis.set must have been called once with the magic_link: prefix
    mock_redis.set.assert_called_once()
    redis_key = mock_redis.set.call_args.args[0]
    assert redis_key.startswith("magic_link:"), f"Unexpected Redis key: {redis_key!r}"

    # Stored payload must reference the correct appointment and patient
    stored_json = json.loads(mock_redis.set.call_args.args[1])
    assert stored_json["appointment_id"] == appt["id"]
    assert stored_json["patient_id"]     == patient["id"]
    assert stored_json["tenant_id"]      == me["tenant_id"]

    # SMS must have been dispatched to the patient's phone
    mock_twilio_cls.return_value.messages.create.assert_called_once()
    sms_kwargs = mock_twilio_cls.return_value.messages.create.call_args
    assert sms_kwargs.kwargs.get("to") == "+12125551234"   # default phone from helpers


# ── 2. Verify ─────────────────────────────────────────────────────────────────

async def test_magic_link_verify_returns_portal_jwt_and_deletes_token(
    client: AsyncClient,
) -> None:
    """GET /auth/magic-link/verify returns a 30-min portal JWT and single-uses the token."""
    tokens, me, _, patient, appt = await _setup(client)

    token   = "verify-test-token-abc"
    payload = json.dumps({
        "appointment_id": appt["id"],
        "patient_id":     patient["id"],
        "tenant_id":      me["tenant_id"],
    }).encode()

    mock_redis = _make_redis(stored=payload)

    async def _fake_redis() -> AsyncMock:
        return mock_redis

    app.dependency_overrides[get_redis] = _fake_redis

    try:
        resp = await client.get(
            "/api/v1/auth/magic-link/verify",
            params={"token": token},
        )
    finally:
        app.dependency_overrides.pop(get_redis, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Response shape
    assert "access_token" in body
    assert body["token_type"]  == "bearer"
    assert body["expires_in"]  == 1800
    assert body["patient_id"]    == patient["id"]
    assert body["appointment_id"] == appt["id"]

    # Token must be deleted exactly once (single-use)
    mock_redis.delete.assert_called_once()
    deleted_key = mock_redis.delete.call_args.args[0]
    assert deleted_key == f"magic_link:{token}"


# ── 3. Expired / invalid ──────────────────────────────────────────────────────

async def test_magic_link_expired_returns_401(client: AsyncClient) -> None:
    """GET /auth/magic-link/verify with an unknown or expired token returns 401."""
    mock_redis = _make_redis(stored=None)   # cache miss = token never existed or expired

    async def _fake_redis() -> AsyncMock:
        return mock_redis

    app.dependency_overrides[get_redis] = _fake_redis

    try:
        resp = await client.get(
            "/api/v1/auth/magic-link/verify",
            params={"token": "this-token-is-expired-or-never-existed"},
        )
    finally:
        app.dependency_overrides.pop(get_redis, None)

    assert resp.status_code == 401
    detail = resp.json()["detail"].lower()
    assert "expired" in detail or "invalid" in detail
