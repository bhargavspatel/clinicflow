import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_tenant, require_owner
from app.models.user import User
from app.schemas.billing import (
    CheckoutResponse,
    PlanOut,
    PlansResponse,
    PortalRequest,
    PortalResponse,
    SubscribeRequest,
    SubscriptionStatusOut,
)
from app.services import billing_service

router = APIRouter()


@router.get("/plans", response_model=PlansResponse)
async def list_plans(
    _current_user: User = Depends(require_owner),
) -> PlansResponse:
    plans = billing_service.get_plans()
    return PlansResponse(plans=[PlanOut(**p) for p in plans])


@router.post("/subscribe", response_model=CheckoutResponse)
async def subscribe(
    data: SubscribeRequest,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _current_user: User = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
) -> CheckoutResponse:
    try:
        checkout_url = await billing_service.create_subscription(
            db, tenant_id, data.price_id, data.success_url, data.cancel_url
        )
    except billing_service.BillingError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return CheckoutResponse(checkout_url=checkout_url)


@router.get("/subscription", response_model=SubscriptionStatusOut)
async def get_subscription(
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _current_user: User = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
) -> SubscriptionStatusOut:
    try:
        sub = await billing_service.get_subscription_status(db, tenant_id)
    except billing_service.NoSubscriptionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return sub


@router.post("/portal", response_model=PortalResponse)
async def billing_portal(
    data: PortalRequest,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _current_user: User = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
) -> PortalResponse:
    try:
        portal_url = await billing_service.create_portal_session(
            db, tenant_id, data.return_url
        )
    except billing_service.BillingError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return PortalResponse(portal_url=portal_url)
