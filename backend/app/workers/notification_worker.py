"""SMS notification worker.

send_sms_task(ctx, notification_id):
  1. Fetch Notification + Appointment + Patient + Provider User + Tenant
  2. Look up sequence_number from the 'reminded' appointment_event so the
     Redis cache key matches docs/06: sha256(patient_id + appointment_id + seq)
  3. Check Redis cache (24hr TTL); invalidate if appointment.updated_at changed
     (patient rescheduled since the message was generated)
  4. On cache miss: call OpenAI gpt-4o-mini → validate SMSContent schema
     On LLM failure / message=None / non-compliant → rule-based fallback template
     Never retry LLM — go straight to fallback on first failure
  5. Truncate to 160 chars at last word boundary if needed
  6. Send via Twilio (sync client in thread executor)
  7. Update notification: body, twilio_sid, status='sent', sent_at
  8. Return summary dict (Arq persists it as the job result)

Idempotency: if the notification is already in a non-queued/non-failed status
when the task fires, it is skipped to prevent double-sends on retry.
"""
import asyncio
import functools
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

import sentry_sdk
import structlog
from openai import AsyncOpenAI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from twilio.rest import Client as TwilioClient

from app.core.config import get_settings
from app.models.appointment import Appointment
from app.models.appointment_event import AppointmentEvent
from app.models.notification import Notification
from app.models.patient import Patient
from app.models.provider import Provider
from app.models.tenant import Tenant
from app.models.user import User
from app.core.plan_gate import PaymentRequiredError, check_sms_limit, increment_sms_usage
from app.services.magic_link_service import create_magic_link_url

logger = structlog.get_logger(__name__)

_CACHE_TTL_SECONDS = 86_400   # 24 hours (docs/06)

_APPT_TYPE_LABELS: dict[str, str] = {
    "initial_eval":  "Initial Evaluation",
    "follow_up":     "Follow-Up",
    "consultation":  "Consultation",
}


# ── LLM output schema (docs/06) ───────────────────────────────────────────────

class SMSContent(BaseModel):
    message: Optional[str] = None   # None → rule-based fallback
    character_count: int
    compliant: bool


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cache_key(
    patient_id: uuid.UUID,
    appointment_id: uuid.UUID,
    sequence_number: int,
) -> str:
    raw = f"{patient_id}{appointment_id}{sequence_number}".encode()
    return hashlib.sha256(raw).hexdigest()


def _appt_type_label(appointment_type: str) -> str:
    return _APPT_TYPE_LABELS.get(
        appointment_type,
        appointment_type.replace("_", " ").title(),
    )


def _format_scheduled_at(dt: datetime) -> str:
    """'Monday, June 2 at 3:30 PM UTC' — no leading zeros."""
    day  = str(dt.day)
    hour = dt.strftime("%I").lstrip("0") or "12"
    return dt.strftime(f"%A, %B {day} at {hour}:%M %p UTC")


def _build_fallback(
    first_name: str,
    provider_last_name: str,
    scheduled_at: datetime,
    magic_link: str,
    clinic_name: str,
) -> str:
    """Rule-based fallback template from docs/06."""
    date_str = scheduled_at.strftime("%-b %-d")   # "Jun 2" (Linux/macOS)
    time_str = scheduled_at.strftime("%-I:%M %p")  # "3:30 PM"
    return (
        f"Hi {first_name}, reminder: appt with Dr. {provider_last_name} on "
        f"{date_str} at {time_str}. Reply YES to confirm or visit "
        f"{magic_link} to reschedule. – {clinic_name}"
    )


def _truncate_to_160(text: str) -> str:
    """Truncate at last word boundary before 157 chars and append '...'"""
    if len(text) <= 160:
        return text
    truncated = text[:157]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated + "..."


# ── LLM generation (max one attempt, then fallback) ───────────────────────────

async def _generate_sms_body(
    openai_client: AsyncOpenAI,
    model: str,
    appt: Appointment,
    patient: Patient,
    provider_user: User,
    tenant: Tenant,
    magic_link: str,
) -> tuple[str, bool, int]:
    """Return (body, fallback_triggered, tokens_used).

    Calls the LLM exactly once.  Any exception or non-compliant output
    falls through to the rule-based template without a retry.
    """
    first_name          = patient.full_name.split()[0]
    provider_last_name  = provider_user.full_name.split()[-1]
    appt_type_label     = _appt_type_label(appt.appointment_type)
    scheduled_formatted = _format_scheduled_at(appt.scheduled_at)

    system_prompt = (
        f"You are a friendly appointment reminder assistant for {tenant.name}.\n"
        "Generate a short, warm, personalized SMS reminder.\n\n"
        "Rules:\n"
        "- Under 160 characters total (hard limit)\n"
        "- Use the patient's first name\n"
        "- Mention the provider name and appointment type naturally\n"
        "- Include ONE clear call to action: reply YES to confirm or tap the link to reschedule\n"
        "- Never use clinical jargon or alarming language\n"
        "- Never include sensitive information beyond name, provider, and appointment time\n"
        "- If you cannot produce a fully compliant message, return null for message\n"
        'Respond with JSON only: {"message": "...", "character_count": <int>, "compliant": <bool>}'
    )
    user_prompt = (
        f"Patient first name: {first_name}\n"
        f"Provider: Dr. {provider_last_name}\n"
        f"Appointment type: {appt_type_label}\n"
        f"Date and time: {scheduled_formatted}\n"
        f"Reschedule link: {magic_link}"
    )

    tokens_used = 0
    try:
        response = await openai_client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            max_tokens=80,
            temperature=0.4,
        )
        tokens_used = response.usage.total_tokens if response.usage else 0
        raw = response.choices[0].message.content or ""
        content = SMSContent.model_validate_json(raw)

        if content.message and content.compliant and len(content.message) <= 160:
            logger.info(
                "llm_ok",
                model=model,
                tokens_used=tokens_used,
                chars=content.character_count,
            )
            return content.message, False, tokens_used

        logger.warning(
            "llm_fallback",
            reason="non_compliant",
            model=model,
            compliant=content.compliant,
            body_chars=len(content.message) if content.message else None,
            tokens_used=tokens_used,
        )

    except Exception as exc:
        logger.warning(
            "llm_fallback",
            reason="exception",
            model=model,
            error=str(exc),
            tokens_used=tokens_used,
        )

    body = _build_fallback(
        first_name, provider_last_name, appt.scheduled_at, magic_link, tenant.name
    )
    return body, True, tokens_used


# ── Twilio dispatch (sync client in thread executor) ─────────────────────────

async def _send_via_twilio(
    twilio_client: TwilioClient,
    from_number: str,
    to_number: str,
    body: str,
) -> str:
    """Dispatch SMS and return the Twilio message SID."""
    loop = asyncio.get_event_loop()
    message = await loop.run_in_executor(
        None,
        functools.partial(
            twilio_client.messages.create,
            body=body,
            from_=from_number,
            to=to_number,
        ),
    )
    return message.sid  # type: ignore[return-value]


# ── LLM fallback rate monitoring ─────────────────────────────────────────────

_FALLBACK_THRESHOLD = 0.05   # 5%
_MIN_SAMPLE_SIZE    = 20     # don't alert on tiny sample windows
_COUNTER_TTL        = 7_200  # 2 hours — covers the current hour + one previous


async def _track_and_alert_fallback(redis, fallback_triggered: bool) -> None:
    """Increment per-hour counters and fire a Sentry alert if rate exceeds 5%."""
    hour_key     = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    total_key    = f"clinicflow:llm:total:{hour_key}"
    fallback_key = f"clinicflow:llm:fallback:{hour_key}"
    alert_key    = f"clinicflow:llm:alert:{hour_key}"

    total = await redis.incr(total_key)
    await redis.expire(total_key, _COUNTER_TTL)
    if fallback_triggered:
        await redis.incr(fallback_key)
        await redis.expire(fallback_key, _COUNTER_TTL)

    if total < _MIN_SAMPLE_SIZE:
        return

    raw = await redis.get(fallback_key)
    fallbacks = int(raw) if raw else 0
    rate = fallbacks / total

    if rate > _FALLBACK_THRESHOLD:
        # nx=True ensures only the first task to cross the threshold fires the alert
        fired = await redis.set(alert_key, "1", ex=_COUNTER_TTL, nx=True)
        if fired:
            with sentry_sdk.new_scope() as scope:
                scope.set_extra("hour", hour_key)
                scope.set_extra("total_sends", total)
                scope.set_extra("fallback_count", fallbacks)
                scope.set_extra("rate_pct", round(rate * 100, 1))
                sentry_sdk.capture_message(
                    f"LLM fallback rate {rate:.1%} exceeds 5% threshold "
                    f"({fallbacks}/{total} SMS in hour {hour_key})",
                    level="warning",
                )
            logger.warning(
                "llm_fallback_rate_alert",
                rate_pct=round(rate * 100, 1),
                fallbacks=fallbacks,
                total=total,
                hour=hour_key,
            )


# ── Arq task ──────────────────────────────────────────────────────────────────

async def send_sms_task(ctx: dict, notification_id: uuid.UUID) -> dict:
    """Send a single queued SMS reminder.

    Returns a summary dict persisted by Arq as the job result:
        {notification_id, model_used, tokens_used, fallback_triggered, twilio_sid}
    """
    factory: async_sessionmaker[AsyncSession] = ctx["db_factory"]
    redis                                     = ctx["redis"]
    openai_client: AsyncOpenAI                = ctx["openai_client"]
    twilio_client: TwilioClient               = ctx["twilio_client"]
    settings                                  = get_settings()

    # ── 1. Fetch all data in one session ──────────────────────────────────────
    async with factory() as db:
        notif = await db.get(Notification, notification_id)
        if notif is None:
            raise ValueError(f"Notification {notification_id} not found")

        # Idempotency: skip if already sent (prevents double-send on Arq retry)
        if notif.status not in ("queued", "failed"):
            logger.info(
                "sms_task_skip",
                notification_id=str(notification_id),
                status=notif.status,
            )
            return {"notification_id": str(notification_id), "skipped": True}

        appt = await db.get(Appointment, notif.appointment_id)
        if appt is None:
            raise ValueError(f"Appointment {notif.appointment_id} not found")

        patient = await db.get(Patient, appt.patient_id)
        if patient is None:
            raise ValueError(f"Patient {appt.patient_id} not found")

        provider = await db.get(Provider, appt.provider_id)
        if provider is None:
            raise ValueError(f"Provider {appt.provider_id} not found")

        provider_user = await db.get(User, provider.user_id)
        if provider_user is None:
            raise ValueError(f"User {provider.user_id} not found")

        tenant = await db.get(Tenant, appt.tenant_id)
        if tenant is None:
            raise ValueError(f"Tenant {appt.tenant_id} not found")

        try:
            await check_sms_limit(db, appt.tenant_id)
        except PaymentRequiredError as exc:
            logger.warning(
                "sms_limit_reached",
                notification_id=str(notification_id),
                tenant_id=str(appt.tenant_id),
                detail=str(exc),
            )
            return {"notification_id": str(notification_id), "skipped": True, "reason": "sms_limit_reached"}

        # Retrieve sequence_number stored in the 'reminded' event for this notification.
        # Used to form the cache key: sha256(patient_id + appointment_id + seq) per docs/06.
        seq_result = await db.execute(
            select(AppointmentEvent).where(
                AppointmentEvent.appointment_id == appt.id,
                AppointmentEvent.event_type == "reminded",
                AppointmentEvent.event_metadata["notification_id"].astext
                == str(notif.id),
            )
        )
        reminded_event = seq_result.scalar_one_or_none()
        sequence_number: int = (
            reminded_event.event_metadata["sequence_number"]
            if reminded_event
            else 1
        )

    # Capture updated_at while the object is still populated (expire_on_commit=False)
    appt_updated_at_iso = appt.updated_at.isoformat()

    magic_link = await create_magic_link_url(
        appt.id, patient.id, appt.tenant_id, tenant.subdomain, redis
    )

    # ── 2. Redis cache check ───────────────────────────────────────────────────
    cache_key   = _cache_key(patient.id, appt.id, sequence_number)
    raw_cached  = await redis.get(cache_key)
    sms_body: Optional[str] = None
    fallback_triggered       = False
    tokens_used              = 0

    if raw_cached:
        cached = json.loads(raw_cached)
        if cached.get("appointment_updated_at") == appt_updated_at_iso:
            sms_body = cached["message"]
            logger.info(
                "sms_cache_hit",
                notification_id=str(notification_id),
                sequence=sequence_number,
            )
        else:
            logger.info(
                "sms_cache_stale",
                notification_id=str(notification_id),
            )

    # ── 3. Generate via LLM / fallback if cache missed ────────────────────────
    if sms_body is None:
        sms_body, fallback_triggered, tokens_used = await _generate_sms_body(
            openai_client,
            settings.openai_sms_model,
            appt,
            patient,
            provider_user,
            tenant,
            magic_link,
        )
        await redis.set(
            cache_key,
            json.dumps({
                "message": sms_body,
                "appointment_updated_at": appt_updated_at_iso,
            }),
            ex=_CACHE_TTL_SECONDS,
        )

    # ── 4. Enforce 160-char hard limit ─────────────────────────────────────────
    sms_body = _truncate_to_160(sms_body)

    # ── 5. Dispatch via Twilio ─────────────────────────────────────────────────
    try:
        twilio_sid = await _send_via_twilio(
            twilio_client,
            settings.twilio_from_number,
            patient.phone,
            sms_body,
        )
    except Exception as exc:
        logger.warning(
            "sms_send_failed",
            notification_id=str(notification_id),
            tenant_id=str(appt.tenant_id),
            patient_id=str(appt.patient_id),
            error=str(exc),
        )
        raise

    # ── 6. Persist result + increment SMS counter in one transaction ──────────
    async with factory() as db:
        notif = await db.get(Notification, notification_id)
        if notif is not None:
            notif.body       = sms_body
            notif.twilio_sid = twilio_sid
            notif.status     = "sent"
            notif.sent_at    = datetime.now(timezone.utc)
        await increment_sms_usage(db, appt.tenant_id)
        await db.commit()

    await redis.publish(
        f"clinicflow:ws:{appt.tenant_id}",
        json.dumps({
            "event": "notification.sent",
            "data": {
                "notification_id": str(notification_id),
                "appointment_id":  str(appt.id),
                "patient_id":      str(appt.patient_id),
            },
        }),
    )

    # ── 7. Log summary + track fallback rate ──────────────────────────────────
    logger.info(
        "sms_sent",
        notification_id=str(notification_id),
        model=settings.openai_sms_model,
        tokens_used=tokens_used,
        fallback=fallback_triggered,
        twilio_sid=twilio_sid,
    )

    await _track_and_alert_fallback(redis, fallback_triggered)

    return {
        "notification_id":    str(notification_id),
        "model_used":         settings.openai_sms_model,
        "tokens_used":        tokens_used,
        "fallback_triggered": fallback_triggered,
        "twilio_sid":         twilio_sid,
    }
