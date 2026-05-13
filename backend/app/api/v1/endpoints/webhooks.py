"""Webhook handlers for third-party services.

POST /webhooks/twilio — inbound SMS from Twilio
  1. Verify X-Twilio-Signature (403 on failure — Twilio does not retry 403)
  2. Parse MessageSid / From / Body from the form payload
  3. Idempotency check: if MessageSid already processed, return cached TwiML
  4. Call handle_inbound_reply() — confirms appointment on YES, records intent
  5. Return TwiML <Message> response (always HTTP 200 — non-200 triggers Twilio retries)
  6. Cache successful TwiML response under the MessageSid (24 hr TTL)

POST /webhooks/stripe — Stripe subscription lifecycle events
  1. Verify Stripe-Signature (403 on failure)
  2. Idempotency check by Stripe event ID (Redis, 7-day TTL)
  3. Route to handler by event type:
       customer.subscription.created  → upsert Subscription, activate tenant
       customer.subscription.updated  → upsert Subscription, update plan_tier
       customer.subscription.deleted  → mark canceled, deactivate tenant
       invoice.payment_failed         → subscription.status = "past_due"
       invoice.payment_succeeded      → reset sms_usage_count to 0
  4. Mark event ID processed in Redis on success (Stripe retries on non-2xx)

NOTE: The Twilio signature is computed against the full request URL.  If this
service runs behind a TLS-terminating proxy, request.url will show "http://".
Add an X-Forwarded-Proto → scheme rewrite in your proxy or set a
WEBHOOK_BASE_URL config value and replace str(request.url) below.
"""
from datetime import datetime, timezone
from typing import Any

import stripe
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.redis import get_redis
from app.core.ws_events import broadcast
from app.models.subscription import Subscription
from app.models.tenant import Tenant
from app.services.notification_service import NoActiveAppointmentError, handle_inbound_reply

logger = structlog.get_logger(__name__)

router = APIRouter()

_IDEMPOTENCY_TTL = 86_400        # 24 hours — long enough to absorb any Twilio retry window
_STRIPE_IDEM_TTL = 7 * 86_400   # 7 days  — Stripe retries webhooks for up to 3 days


def _twiml(text: str) -> str:
    """Build a TwiML <Response><Message> XML string."""
    resp = MessagingResponse()
    resp.message(text)
    return str(resp)


@router.post(
    "/twilio",
    status_code=status.HTTP_200_OK,
    response_class=Response,
    include_in_schema=False,  # not a public API — hide from OpenAPI docs
)
async def twilio_inbound_sms(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> Response:
    """Handle inbound SMS reply from Twilio."""
    settings = get_settings()

    # ── 1. Read the full form payload (needed for signature validation) ────────
    # Twilio sends application/x-www-form-urlencoded; all fields must be present
    # in the params dict for the HMAC to verify correctly.
    form = await request.form()
    params: dict[str, str] = {k: str(v) for k, v in form.items()}

    # ── 2. Verify Twilio signature ─────────────────────────────────────────────
    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing X-Twilio-Signature",
        )

    validator = RequestValidator(settings.twilio_auth_token)
    if not validator.validate(str(request.url), params, signature):
        logger.warning(
            "invalid_webhook_signature",
            webhook="twilio",
            url=str(request.url),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Twilio signature",
        )

    # ── 3. Extract required fields ─────────────────────────────────────────────
    message_sid = params.get("MessageSid", "")
    from_number = params.get("From", "")
    body        = params.get("Body", "")

    if not message_sid or not from_number:
        # Malformed Twilio request — log and return empty TwiML (200 to stop retries)
        logger.error("twilio_missing_fields", fields=list(params.keys()))
        return Response(content=_twiml(""), media_type="application/xml")

    # ── 4. Idempotency: return cached TwiML if this MessageSid was already processed
    idempotency_key = f"twilio:inbound:{message_sid}"
    cached = await redis.get(idempotency_key)
    if cached:
        logger.info("twilio_duplicate", message_sid=message_sid)
        return Response(
            content=cached.decode() if isinstance(cached, bytes) else cached,
            media_type="application/xml",
        )

    # ── 5. Call service — always return 200 + TwiML, never let exceptions surface ─
    xml_content: str
    try:
        result = await handle_inbound_reply(db, from_number, body)

        if result.intent == "confirmed":
            reply_text = (
                "Your appointment is confirmed! We look forward to seeing you. "
                "Reply RESCHEDULE anytime to change it."
            )
        elif result.intent == "reschedule_requested":
            reply_text = (
                "To reschedule, tap the link in your earlier reminder message. "
                "Your appointment has not been changed yet."
            )
        else:  # unrecognized
            reply_text = (
                "Reply YES to confirm your appointment, "
                "or tap the reschedule link in your reminder."
            )

        xml_content = _twiml(reply_text)

        # Cache successful response for idempotency
        await redis.set(idempotency_key, xml_content, ex=_IDEMPOTENCY_TTL)

        await broadcast(
            redis,
            str(result.tenant_id),
            "notification.received",
            {
                "from_number":    from_number,
                "intent":         result.intent,
                "appointment_id": str(result.appointment_id),
            },
        )

        logger.info(
            "twilio_inbound",
            message_sid=message_sid,
            intent=result.intent,
            appointment_id=str(result.appointment_id),
        )

    except NoActiveAppointmentError:
        # No patient/appointment found — still 200 so Twilio doesn't retry
        xml_content = _twiml(
            "We couldn't find an upcoming appointment for your number. "
            "Please contact your clinic directly."
        )
        logger.info("twilio_no_appointment", message_sid=message_sid)
        # Do NOT cache — a future appointment may exist after a retry

    except Exception:
        # Unexpected error — log fully, return generic TwiML, do not cache
        logger.exception("twilio_error", message_sid=message_sid)
        xml_content = _twiml(
            "We're having trouble processing your reply right now. "
            "Please try again in a few minutes."
        )
        # Do NOT cache — the service may recover on retry

    return Response(content=xml_content, media_type="application/xml")


# ── Stripe webhook ────────────────────────────────────────────────────────────

def _plan_from_price_id(price_id: str, settings: Settings) -> tuple[str, int]:
    """Map a Stripe price ID to (plan_tier, sms_limit). Falls back to starter."""
    if price_id == settings.stripe_starter_price_id:
        return "starter", 500
    if price_id == settings.stripe_growth_price_id:
        return "growth", 2000
    logger.warning("stripe_unknown_price_id", price_id=price_id)
    return "starter", 500


async def _upsert_subscription(
    db: AsyncSession,
    sub_obj: Any,
    settings: Settings,
    *,
    activate_tenant: bool,
) -> None:
    """Create or update the Subscription row and tenant plan_tier.

    activate_tenant=True  for customer.subscription.created
    activate_tenant=False for customer.subscription.updated (don't touch is_active)
    """
    stripe_sub_id      = sub_obj["id"]
    stripe_customer_id = sub_obj["customer"]
    stripe_status      = sub_obj["status"]
    period_end         = datetime.fromtimestamp(sub_obj["current_period_end"], tz=timezone.utc)

    items = sub_obj.get("items", {}).get("data", [])
    stripe_price_id = items[0]["price"]["id"] if items else ""
    plan_tier, sms_limit = _plan_from_price_id(stripe_price_id, settings)

    tenant = (
        await db.execute(select(Tenant).where(Tenant.stripe_customer_id == stripe_customer_id))
    ).scalar_one_or_none()
    if tenant is None:
        logger.warning("stripe_no_tenant", stripe_customer_id=stripe_customer_id)
        return

    sub = (
        await db.execute(select(Subscription).where(Subscription.tenant_id == tenant.id))
    ).scalar_one_or_none()

    if sub is None:
        sub = Subscription(
            tenant_id=tenant.id,
            stripe_subscription_id=stripe_sub_id,
            stripe_price_id=stripe_price_id,
            status=stripe_status,
            current_period_end=period_end,
            sms_usage_count=0,
            sms_usage_limit=sms_limit,
        )
        db.add(sub)
    else:
        sub.stripe_subscription_id = stripe_sub_id
        sub.stripe_price_id        = stripe_price_id
        sub.status                 = stripe_status
        sub.current_period_end     = period_end
        sub.sms_usage_limit        = sms_limit

    tenant.plan_tier = plan_tier
    if activate_tenant:
        tenant.is_active = True

    await db.commit()


async def _handle_subscription_deleted(db: AsyncSession, sub_obj: Any) -> None:
    """Mark subscription canceled and deactivate the tenant."""
    stripe_sub_id = sub_obj["id"]

    sub = (
        await db.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == stripe_sub_id)
        )
    ).scalar_one_or_none()
    if sub is None:
        logger.warning("stripe_sub_not_found", stripe_sub_id=stripe_sub_id, event="deleted")
        return

    sub.status = "canceled"

    tenant = await db.get(Tenant, sub.tenant_id)
    if tenant is not None:
        tenant.is_active = False

    await db.commit()


async def _handle_invoice_payment_failed(db: AsyncSession, invoice_obj: Any) -> None:
    """Set subscription status to past_due on a failed invoice payment."""
    stripe_sub_id = invoice_obj.get("subscription")
    if not stripe_sub_id:
        return  # one-time invoice, not subscription-related

    sub = (
        await db.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == stripe_sub_id)
        )
    ).scalar_one_or_none()
    if sub is None:
        logger.warning("stripe_sub_not_found", stripe_sub_id=stripe_sub_id, event="payment_failed")
        return

    sub.status = "past_due"
    await db.commit()


async def _handle_invoice_payment_succeeded(db: AsyncSession, invoice_obj: Any) -> None:
    """Reset SMS usage counter at the start of a new billing period."""
    stripe_sub_id = invoice_obj.get("subscription")
    if not stripe_sub_id:
        return

    sub = (
        await db.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == stripe_sub_id)
        )
    ).scalar_one_or_none()
    if sub is None:
        logger.warning("stripe_sub_not_found", stripe_sub_id=stripe_sub_id, event="payment_succeeded")
        return

    sub.sms_usage_count = 0
    await db.commit()


@router.post(
    "/stripe",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def stripe_inbound_event(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict[str, str]:
    """Handle Stripe subscription lifecycle events."""
    settings = get_settings()

    # ── 1. Read raw body — must be bytes for HMAC verification ───────────────
    payload    = await request.body()
    sig_header = request.headers.get("Stripe-Signature", "")

    if not sig_header:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing Stripe-Signature header",
        )

    # ── 2. Verify signature ───────────────────────────────────────────────────
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
    except stripe.error.SignatureVerificationError:
        logger.warning("invalid_webhook_signature", webhook="stripe")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Stripe signature",
        )
    except ValueError:
        logger.warning("invalid_webhook_signature", webhook="stripe", reason="malformed_payload")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid payload",
        )

    event_id   = event["id"]
    event_type = event["type"]
    data_obj   = event["data"]["object"]

    # ── 3. Idempotency check ──────────────────────────────────────────────────
    idem_key = f"stripe:event:{event_id}"
    if await redis.get(idem_key):
        logger.info("stripe_duplicate", event_id=event_id)
        return {"status": "already_processed"}

    # ── 4. Route to handler — non-2xx causes Stripe to retry ─────────────────
    if event_type == "customer.subscription.created":
        await _upsert_subscription(db, data_obj, settings, activate_tenant=True)
    elif event_type == "customer.subscription.updated":
        await _upsert_subscription(db, data_obj, settings, activate_tenant=False)
    elif event_type == "customer.subscription.deleted":
        await _handle_subscription_deleted(db, data_obj)
    elif event_type == "invoice.payment_failed":
        await _handle_invoice_payment_failed(db, data_obj)
    elif event_type == "invoice.payment_succeeded":
        await _handle_invoice_payment_succeeded(db, data_obj)
    else:
        logger.info("stripe_unhandled_event", event_type=event_type, event_id=event_id)

    # ── 5. Mark processed (only on success — failure keeps key absent so Stripe retries)
    await redis.set(idem_key, "1", ex=_STRIPE_IDEM_TTL)

    logger.info("stripe_event", event_type=event_type, event_id=event_id)
    return {"status": "ok"}
