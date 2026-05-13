"""Integration tests for the Twilio inbound-SMS webhook.

External calls mocked:
  - twilio.request_validator.RequestValidator.validate  (Twilio HMAC check)
  - app.core.redis.get_redis                            (Redis dependency)

The database is the real clinicflow_test instance (via `client` fixture).
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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

# Twilio validator patch target — patch the class method so all instances are affected
_VALIDATOR_PATH = "app.api.v1.endpoints.webhooks.RequestValidator.validate"

# Reusable form payload helpers
_FROM_NUMBER = "+12125551234"   # matches helpers.create_patient default phone


def _twilio_form(
    body: str = "YES",
    from_number: str = _FROM_NUMBER,
    message_sid: str | None = None,
) -> dict:
    return {
        "MessageSid": message_sid or f"SM{uuid.uuid4().hex}",
        "From":       from_number,
        "Body":       body,
    }


async def _setup(client: AsyncClient) -> tuple[dict, dict]:
    """Register clinic, create provider + patient + appointment. Return (tokens, appt)."""
    tokens   = await register_clinic(client, _CLINIC_A)
    me       = await get_me(client, tokens)
    provider = await create_provider(client, tokens, me["id"])
    patient  = await create_patient(client, tokens, phone=_FROM_NUMBER)
    appt     = await create_appointment(
        client, tokens, patient["id"], provider["id"],
        scheduled_at="2026-09-01T10:00:00Z",
        appointment_type="follow_up",
    )
    return tokens, appt


def _mock_redis() -> AsyncMock:
    """Return an AsyncMock Redis that always reports a cache miss."""
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.set = AsyncMock()
    return r


# ── Signature validation ──────────────────────────────────────────────────────

async def test_invalid_twilio_signature_returns_403(client: AsyncClient) -> None:
    """A request with a bad X-Twilio-Signature is rejected with 403 before any DB work."""
    with patch(_VALIDATOR_PATH, return_value=False):
        resp = await client.post(
            "/api/v1/webhooks/twilio",
            data=_twilio_form(),
            headers={"X-Twilio-Signature": "bad-signature"},
        )

    assert resp.status_code == 403


async def test_missing_signature_header_returns_403(client: AsyncClient) -> None:
    """A request with no X-Twilio-Signature header is rejected with 403."""
    resp = await client.post(
        "/api/v1/webhooks/twilio",
        data=_twilio_form(),
        # deliberately no X-Twilio-Signature header
    )

    assert resp.status_code == 403


# ── Inbound reply: YES confirms appointment ───────────────────────────────────

async def test_yes_reply_confirms_appointment(
    client: AsyncClient,
    seed_db: AsyncSession,
) -> None:
    """A 'YES' SMS from a patient confirms their nearest upcoming appointment.

    Verifies both the TwiML response body and the persisted DB state change.
    """
    from app.core.redis import get_redis
    from app.main import app

    tokens, appt = await _setup(client)

    # Override Redis so the webhook doesn't need a live Redis connection
    mock_redis = _mock_redis()

    async def _fake_redis() -> AsyncMock:
        return mock_redis

    app.dependency_overrides[get_redis] = _fake_redis

    try:
        with patch(_VALIDATOR_PATH, return_value=True):
            resp = await client.post(
                "/api/v1/webhooks/twilio",
                data=_twilio_form(body="YES"),
                headers={"X-Twilio-Signature": "valid"},
            )
    finally:
        # Remove the Redis override so other tests are unaffected
        app.dependency_overrides.pop(get_redis, None)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/xml")
    assert "confirmed" in resp.text.lower()

    # Verify the appointment row was updated in the DB
    seed_db.expire_all()
    row = (
        await seed_db.execute(
            select(Appointment).where(Appointment.id == uuid.UUID(appt["id"]))
        )
    ).scalar_one()
    assert row.status == "confirmed"


async def test_yes_reply_returns_twiml_xml(client: AsyncClient) -> None:
    """Response is well-formed TwiML XML regardless of intent."""
    from app.core.redis import get_redis
    from app.main import app

    await _setup(client)

    async def _fake_redis() -> AsyncMock:
        return _mock_redis()

    app.dependency_overrides[get_redis] = _fake_redis

    try:
        with patch(_VALIDATOR_PATH, return_value=True):
            resp = await client.post(
                "/api/v1/webhooks/twilio",
                data=_twilio_form(body="YES"),
                headers={"X-Twilio-Signature": "valid"},
            )
    finally:
        app.dependency_overrides.pop(get_redis, None)

    assert resp.status_code == 200
    assert "<?xml" in resp.text
    assert "<Response>" in resp.text
    assert "<Message>" in resp.text


async def test_duplicate_message_sid_returns_cached_twiml(client: AsyncClient) -> None:
    """A second POST with the same MessageSid returns the cached TwiML (idempotency)."""
    from app.core.redis import get_redis
    from app.main import app

    await _setup(client)

    sid          = f"SM{uuid.uuid4().hex}"
    cached_xml   = '<?xml version="1.0" encoding="UTF-8"?><Response><Message>cached</Message></Response>'
    mock_redis   = AsyncMock()
    mock_redis.get = AsyncMock(return_value=cached_xml.encode())
    mock_redis.set = AsyncMock()

    async def _fake_redis() -> AsyncMock:
        return mock_redis

    app.dependency_overrides[get_redis] = _fake_redis

    try:
        with patch(_VALIDATOR_PATH, return_value=True):
            resp = await client.post(
                "/api/v1/webhooks/twilio",
                data=_twilio_form(body="YES", message_sid=sid),
                headers={"X-Twilio-Signature": "valid"},
            )
    finally:
        app.dependency_overrides.pop(get_redis, None)

    assert resp.status_code == 200
    assert "cached" in resp.text
