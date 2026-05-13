"""Magic link generation, verification, and patient portal JWT issuance.

Flow:
  1. Clinic staff triggers POST /auth/magic-link/send with an appointment_id.
  2. send_magic_link() generates a secure random token, stores it in Redis for
     30 minutes, and sends an SMS to the patient with the portal URL.
  3. Patient clicks the link → GET /auth/magic-link/verify?token=...
  4. verify_magic_link() validates the token and returns the stored payload.
  5. generate_patient_jwt() issues a 30-minute portal JWT the patient uses to
     call the reschedule / waitlist endpoints.

Tokens are single-use: verify_magic_link() deletes the Redis entry on success.
"""
import asyncio
import functools
import json
import secrets
import uuid
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_portal_token
from app.models.appointment import Appointment
from app.models.patient import Patient
from app.models.tenant import Tenant

_TOKEN_TTL = 1800  # 30 minutes in seconds
_CACHE_PREFIX = "magic_link:"
_ACTIVE_STATUSES = frozenset({"scheduled", "rescheduled", "confirmed"})


class MagicLinkError(Exception):
    pass


async def create_magic_link_url(
    appointment_id: uuid.UUID,
    patient_id: uuid.UUID,
    tenant_id: uuid.UUID,
    subdomain: str,
    redis: Redis,
) -> str:
    """Generate a magic link token, cache it, and return the full portal URL.

    Separated from send_magic_link so the notification worker can embed a real
    reschedule URL in reminder messages without triggering a second SMS send.
    """
    token = secrets.token_urlsafe(32)
    payload = json.dumps({
        "appointment_id": str(appointment_id),
        "patient_id":     str(patient_id),
        "tenant_id":      str(tenant_id),
    })
    await redis.set(f"{_CACHE_PREFIX}{token}", payload, ex=_TOKEN_TTL)
    return f"https://{subdomain}.clinicflow.app/reschedule?token={token}"


async def send_magic_link(
    db: AsyncSession,
    appointment_id: uuid.UUID,
    tenant_id: uuid.UUID,
    redis: Redis,
    twilio_client: Any,
    from_number: str,
) -> str:
    """Generate a signed magic link, cache it, and SMS the patient.

    tenant_id must match the appointment's tenant — prevents cross-tenant leaks.
    Returns the raw token (callers don't need it but tests can verify it).
    """
    appt = (
        await db.execute(select(Appointment).where(Appointment.id == appointment_id))
    ).scalar_one_or_none()

    if appt is None or appt.tenant_id != tenant_id:
        raise MagicLinkError(f"Appointment {appointment_id} not found")

    if appt.status not in _ACTIVE_STATUSES:
        raise MagicLinkError(
            f"Cannot send magic link for appointment with status '{appt.status}'"
        )

    patient = (
        await db.execute(select(Patient).where(Patient.id == appt.patient_id))
    ).scalar_one_or_none()
    if patient is None:
        raise MagicLinkError(f"Patient {appt.patient_id} not found")

    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if tenant is None:
        raise MagicLinkError(f"Tenant {tenant_id} not found")

    portal_url = await create_magic_link_url(
        appointment_id, appt.patient_id, tenant_id, tenant.subdomain, redis
    )
    first_name = patient.full_name.split()[0]
    sms_body = (
        f"Hi {first_name}, tap this link to reschedule your appointment: "
        f"{portal_url} — {tenant.name}"
    )

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        functools.partial(
            twilio_client.messages.create,
            body=sms_body,
            from_=from_number,
            to=patient.phone,
        ),
    )

    # Return the raw token (last segment after the final /)
    return portal_url.rsplit("=", 1)[-1]


async def verify_magic_link(token: str, redis: Redis) -> dict[str, str]:
    """Validate a magic link token and return its payload.

    Single-use: deletes the Redis entry on success so the link cannot be replayed.
    Raises MagicLinkError if the token is invalid or expired.
    """
    raw = await redis.get(f"{_CACHE_PREFIX}{token}")
    if raw is None:
        raise MagicLinkError("Magic link is invalid or has expired")

    await redis.delete(f"{_CACHE_PREFIX}{token}")
    return json.loads(raw)


def generate_patient_jwt(
    patient_id: uuid.UUID,
    appointment_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> str:
    """Issue a 30-minute portal JWT for a patient."""
    return create_portal_token(patient_id, appointment_id, tenant_id)
