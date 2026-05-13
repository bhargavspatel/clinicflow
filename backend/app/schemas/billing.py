from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PlanOut(BaseModel):
    id: str
    name: str
    price_usd_cents: int
    sms_limit: int
    description: str
    price_id: str


class PlansResponse(BaseModel):
    plans: list[PlanOut]


class SubscribeRequest(BaseModel):
    price_id: str
    success_url: str = "https://app.clinicflow.app/billing/success"
    cancel_url: str = "https://app.clinicflow.app/billing/cancel"


class CheckoutResponse(BaseModel):
    checkout_url: str


class SubscriptionStatusOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    stripe_subscription_id: str
    stripe_price_id: str
    status: str
    current_period_end: datetime
    sms_usage_count: int
    sms_usage_limit: int


class PortalRequest(BaseModel):
    return_url: str = "https://app.clinicflow.app/billing"


class PortalResponse(BaseModel):
    portal_url: str
