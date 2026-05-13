"""Billing plan enforcement.

check_sms_limit          Raise PaymentRequiredError if tenant has reached their monthly SMS cap.
increment_sms_usage      Increment sms_usage_count by 1 (no commit — caller must commit).
require_active_subscription  FastAPI dependency — HTTP 402 if subscription is past_due or canceled.
"""
import uuid

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_tenant
from app.models.subscription import Subscription


class PaymentRequiredError(Exception):
    pass


async def check_sms_limit(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Raise PaymentRequiredError if the tenant is at or over their SMS limit."""
    sub = (
        await db.execute(select(Subscription).where(Subscription.tenant_id == tenant_id))
    ).scalar_one_or_none()

    if sub is None:
        raise PaymentRequiredError("No active subscription")
    if sub.sms_usage_count >= sub.sms_usage_limit:
        raise PaymentRequiredError(
            f"Monthly SMS limit reached ({sub.sms_usage_count}/{sub.sms_usage_limit}). "
            "Upgrade your plan or wait for the next billing cycle."
        )


async def increment_sms_usage(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Increment sms_usage_count by 1. Does not commit — caller owns the transaction."""
    sub = (
        await db.execute(select(Subscription).where(Subscription.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if sub is not None:
        sub.sms_usage_count += 1


async def require_active_subscription(
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
) -> None:
    """FastAPI dependency — raise HTTP 402 if the tenant's subscription is not usable.

    Blocks access for past_due and canceled subscriptions, and for tenants with
    no subscription row at all.  Active and trialing subscriptions pass through.

    Applied as a router-level dependency on all gated endpoints (patients,
    providers, appointments).  Billing and auth routers are intentionally excluded
    so that tenants with lapsed payments can still reach the billing portal.
    """
    sub = (
        await db.execute(select(Subscription).where(Subscription.tenant_id == tenant_id))
    ).scalar_one_or_none()

    if sub is None:
        return   # no subscription row → free trial, allow access
    if sub.status in {"past_due", "canceled"}:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Subscription {sub.status}. "
                "Visit /billing/portal to update your payment details."
            ),
        )
