"""Tests for POST /webhooks/stripe and the plan-gate subscription guard.

Stripe API calls are fully mocked — no network calls are made.

External calls mocked:
  - app.api.v1.endpoints.webhooks.stripe.Webhook.construct_event
  - app.core.redis.get_redis  (FastAPI dependency override)

Database: real clinicflow_test instance (via client / seed_db fixtures).

Covered scenarios
-----------------
1. invalid_signature    — SignatureVerificationError → 400
2. subscription_deleted — customer.subscription.deleted deactivates tenant
3. payment_succeeded    — invoice.payment_succeeded resets sms_usage_count to 0
4. suspended_tenant_402 — past_due subscription → 402 on gated endpoint
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import stripe
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.main import app
from app.models.subscription import Subscription
from app.models.tenant import Tenant
from tests.api.helpers import _CLINIC_A, auth, get_me, register_clinic

_STRIPE_PATCH = "app.api.v1.endpoints.webhooks.stripe.Webhook.construct_event"
_FAKE_SIG     = "t=1234,v1=fakehash"
_SUB_ID       = "sub_test_billing_001"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_redis() -> AsyncMock:
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)   # no prior idempotency hit
    r.set = AsyncMock()
    return r


async def _seed_subscription(
    seed_db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    sub_id: str = _SUB_ID,
    status: str = "active",
    sms_usage_count: int = 0,
    sms_usage_limit: int = 500,
) -> Subscription:
    sub = Subscription(
        tenant_id=tenant_id,
        stripe_subscription_id=sub_id,
        stripe_price_id="price_starter_test",
        status=status,
        current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
        sms_usage_count=sms_usage_count,
        sms_usage_limit=sms_usage_limit,
    )
    seed_db.add(sub)
    await seed_db.commit()
    return sub


async def _post_webhook(client: AsyncClient, event: dict) -> AsyncClient:
    """POST a mocked Stripe event; injects a Redis mock for idempotency."""
    mock_redis = _make_redis()

    async def _fake_redis() -> AsyncMock:
        return mock_redis

    app.dependency_overrides[get_redis] = _fake_redis
    try:
        with patch(_STRIPE_PATCH, return_value=event):
            return await client.post(
                "/api/v1/webhooks/stripe",
                content=b"{}",
                headers={"Stripe-Signature": _FAKE_SIG},
            )
    finally:
        app.dependency_overrides.pop(get_redis, None)


# ── 1. Invalid signature ──────────────────────────────────────────────────────

async def test_stripe_invalid_signature_returns_400(client: AsyncClient) -> None:
    """construct_event raising SignatureVerificationError → 400."""
    with patch(
        _STRIPE_PATCH,
        side_effect=stripe.error.SignatureVerificationError("bad sig", "sig"),
    ):
        resp = await client.post(
            "/api/v1/webhooks/stripe",
            content=b"{}",
            headers={"Stripe-Signature": "t=bad,v1=badhash"},
        )

    assert resp.status_code == 400, resp.text
    assert "signature" in resp.json()["detail"].lower()


# ── 2. subscription.deleted → tenant deactivated ─────────────────────────────

async def test_stripe_subscription_deleted_deactivates_tenant(
    client: AsyncClient,
    seed_db: AsyncSession,
) -> None:
    """customer.subscription.deleted sets tenant.is_active=False, sub.status='canceled'."""
    tokens    = await register_clinic(client, _CLINIC_A)
    me        = await get_me(client, tokens)
    tenant_id = uuid.UUID(me["tenant_id"])

    await _seed_subscription(seed_db, tenant_id, sub_id=_SUB_ID, status="active")

    event = {
        "id":   "evt_deleted_001",
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "id":                  _SUB_ID,
                "customer":            "cus_test_001",
                "status":              "canceled",
                "current_period_end":  1_893_456_000,
                "items": {"data": [{"price": {"id": "price_starter_test"}}]},
            }
        },
    }

    resp = await _post_webhook(client, event)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok"

    seed_db.expire_all()

    sub = (
        await seed_db.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == _SUB_ID)
        )
    ).scalar_one()
    assert sub.status == "canceled"

    tenant = (
        await seed_db.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one()
    assert tenant.is_active is False


# ── 3. invoice.payment_succeeded → SMS count reset ───────────────────────────

async def test_stripe_payment_succeeded_resets_sms_count(
    client: AsyncClient,
    seed_db: AsyncSession,
) -> None:
    """invoice.payment_succeeded resets sms_usage_count to 0."""
    tokens    = await register_clinic(client, _CLINIC_A)
    me        = await get_me(client, tokens)
    tenant_id = uuid.UUID(me["tenant_id"])

    await _seed_subscription(seed_db, tenant_id, sub_id=_SUB_ID, sms_usage_count=347)

    event = {
        "id":   "evt_invoice_001",
        "type": "invoice.payment_succeeded",
        "data": {
            "object": {
                "id":           "in_test_001",
                "subscription": _SUB_ID,
                "customer":     "cus_test_001",
            }
        },
    }

    resp = await _post_webhook(client, event)
    assert resp.status_code == 200, resp.text

    seed_db.expire_all()

    sub = (
        await seed_db.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == _SUB_ID)
        )
    ).scalar_one()
    assert sub.sms_usage_count == 0


# ── 4. Suspended tenant → 402 ─────────────────────────────────────────────────

async def test_past_due_tenant_returns_402_on_gated_endpoint(
    client: AsyncClient,
    seed_db: AsyncSession,
) -> None:
    """A past_due subscription blocks gated endpoints with HTTP 402."""
    tokens    = await register_clinic(client, _CLINIC_A)
    me        = await get_me(client, tokens)
    tenant_id = uuid.UUID(me["tenant_id"])

    await _seed_subscription(seed_db, tenant_id, status="past_due")

    resp = await client.get("/api/v1/patients", headers=auth(tokens))
    assert resp.status_code == 402, resp.text
    assert "past_due" in resp.json()["detail"]
