"""Billing service — pure Python, no FastAPI imports.

get_plans              Return hardcoded plan catalogue with Stripe price IDs from settings.
create_subscription    Create a Stripe Checkout Session for a new subscription.
get_subscription_status  Return the Subscription DB row for a tenant.
create_portal_session  Create a Stripe Billing Portal session for self-service management.

Stripe API calls are synchronous (stripe-python library); each is dispatched to a
thread pool via run_in_executor so the async event loop is never blocked.
"""
import asyncio
import functools
import uuid
from typing import Any

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.subscription import Subscription
from app.models.tenant import Tenant
from app.models.user import User


# ── Domain exceptions ─────────────────────────────────────────────────────────

class BillingError(Exception):
    pass


class NoSubscriptionError(Exception):
    pass


# ── Plan catalogue ────────────────────────────────────────────────────────────

_PLANS: list[dict[str, Any]] = [
    {
        "id": "starter",
        "name": "Starter",
        "price_usd_cents": 14900,
        "sms_limit": 500,
        "description": "Up to 500 SMS/month",
    },
    {
        "id": "growth",
        "name": "Growth",
        "price_usd_cents": 24900,
        "sms_limit": 2000,
        "description": "Up to 2000 SMS/month",
    },
]


def get_plans() -> list[dict[str, Any]]:
    """Return plan catalogue with Stripe price IDs injected from settings."""
    settings = get_settings()
    price_map = {
        "starter": settings.stripe_starter_price_id,
        "growth":  settings.stripe_growth_price_id,
    }
    return [{**plan, "price_id": price_map[plan["id"]]} for plan in _PLANS]


# ── Stripe helpers ────────────────────────────────────────────────────────────

async def _stripe(func: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a synchronous Stripe API call in the default thread-pool executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))


async def _get_or_create_stripe_customer(
    db: AsyncSession,
    tenant: Tenant,
    api_key: str,
) -> str:
    """Return the tenant's Stripe customer ID, creating one in Stripe if absent."""
    if tenant.stripe_customer_id:
        return tenant.stripe_customer_id

    owner = (
        await db.execute(
            select(User).where(
                User.tenant_id == tenant.id,
                User.role == "owner",
            )
        )
    ).scalar_one_or_none()

    customer = await _stripe(
        stripe.Customer.create,
        api_key=api_key,
        name=tenant.name,
        email=owner.email if owner else None,
        metadata={"tenant_id": str(tenant.id)},
    )
    tenant.stripe_customer_id = customer.id
    await db.flush()
    return customer.id


# ── Service functions ─────────────────────────────────────────────────────────

async def create_subscription(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    price_id: str,
    success_url: str,
    cancel_url: str,
) -> str:
    """Create a Stripe Checkout Session for a new subscription.

    Returns the Checkout Session URL to redirect the browser to.
    The Subscription DB row is created later by the Stripe webhook
    (customer.subscription.created / checkout.session.completed).

    Raises:
        BillingError: unknown price_id, tenant not found, or subscription
            already active.
    """
    settings = get_settings()
    api_key = settings.stripe_secret_key

    valid_price_ids = {settings.stripe_starter_price_id, settings.stripe_growth_price_id}
    if price_id not in valid_price_ids:
        raise BillingError(f"Unknown price ID: {price_id!r}")

    existing = (
        await db.execute(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if existing and existing.status in {"active", "trialing"}:
        raise BillingError(
            "Tenant already has an active subscription. Use the billing portal to manage it."
        )

    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise BillingError(f"Tenant {tenant_id} not found")

    customer_id = await _get_or_create_stripe_customer(db, tenant, api_key)

    session = await _stripe(
        stripe.checkout.Session.create,
        api_key=api_key,
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
    )

    await db.commit()
    return session.url


async def get_subscription_status(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> Subscription:
    """Return the Subscription row for the tenant.

    Raises:
        NoSubscriptionError: no subscription row found for this tenant.
    """
    sub = (
        await db.execute(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()

    if sub is None:
        raise NoSubscriptionError(f"No subscription found for tenant {tenant_id}")
    return sub


async def create_portal_session(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    return_url: str,
) -> str:
    """Create a Stripe Billing Portal session for self-service management.

    Returns the portal URL to redirect the browser to.

    Raises:
        BillingError: tenant not found or has no Stripe customer yet.
    """
    settings = get_settings()
    api_key = settings.stripe_secret_key

    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise BillingError(f"Tenant {tenant_id} not found")
    if not tenant.stripe_customer_id:
        raise BillingError(
            "No Stripe customer associated with this tenant. Subscribe first."
        )

    portal = await _stripe(
        stripe.billing_portal.Session.create,
        api_key=api_key,
        customer=tenant.stripe_customer_id,
        return_url=return_url,
    )
    return portal.url
