"""Waitlist service — zero FastAPI imports.

add_to_waitlist        Add a patient to the tenant waitlist with optional provider
                       preference, day preferences, and time-of-day preference.
find_waitlist_match    Given a cancelled appointment, return the oldest unfilled
                       entry whose provider/day/time preferences match.  Returns
                       None when the appointment is within 2 hours (not worth
                       filling) or no suitable entry exists.
notify_waitlist_patient  Send an SMS to the matched waitlist patient, mark the
                          entry filled, and append a 'waitlist_filled' audit event.
"""
import asyncio
import functools
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.appointment_event import AppointmentEvent
from app.models.notification import Notification
from app.models.patient import Patient
from app.models.provider import Provider
from app.models.tenant import Tenant
from app.models.waitlist import WaitlistEntry

_VALID_PREFERRED_TIMES = frozenset({"morning", "afternoon", "any"})
_VALID_DAYS = frozenset({
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
})
# Minimum hours between now and the appointment for a waitlist fill to be worthwhile.
_MIN_LEAD_HOURS = 2


class WaitlistError(Exception):
    pass


# ── Internal helpers ──────────────────────────────────────────────────────────

def _time_bucket(dt: datetime) -> str:
    return "morning" if dt.hour < 12 else "afternoon"


def _day_name(dt: datetime) -> str:
    return dt.strftime("%A").lower()


def _truncate(text: str, limit: int = 160) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 3]
    last_space = cut.rfind(" ")
    return (cut[:last_space] if last_space > 0 else cut) + "..."


# ── Public service functions ──────────────────────────────────────────────────

async def add_to_waitlist(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    patient_id: uuid.UUID,
    preferred_times: str,
    provider_id: Optional[uuid.UUID] = None,
    preferred_days: Optional[list[str]] = None,
) -> WaitlistEntry:
    """Add a patient to the waitlist.

    Raises WaitlistError on invalid inputs or missing FK targets.
    """
    if preferred_times not in _VALID_PREFERRED_TIMES:
        raise WaitlistError(
            f"preferred_times must be one of {sorted(_VALID_PREFERRED_TIMES)}"
        )
    if preferred_days:
        invalid = set(d.lower() for d in preferred_days) - _VALID_DAYS
        if invalid:
            raise WaitlistError(f"Invalid preferred_days: {sorted(invalid)}")
        preferred_days = [d.lower() for d in preferred_days]

    patient = await db.get(Patient, patient_id)
    if patient is None or patient.tenant_id != tenant_id:
        raise WaitlistError(f"Patient {patient_id} not found")

    if provider_id is not None:
        provider = await db.get(Provider, provider_id)
        if provider is None or provider.tenant_id != tenant_id:
            raise WaitlistError(f"Provider {provider_id} not found")

    entry = WaitlistEntry(
        tenant_id=tenant_id,
        patient_id=patient_id,
        provider_id=provider_id,
        preferred_days=preferred_days,
        preferred_times=preferred_times,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def find_waitlist_match(
    db: AsyncSession,
    appointment: Appointment,
) -> Optional[WaitlistEntry]:
    """Return the earliest-added unfilled entry matching the appointment's slot.

    Returns None if the appointment is less than 2 hours away (insufficient lead
    time to fill the slot) or no matching entry exists.

    Matching rules (all must hold):
      - tenant_id matches
      - filled_at is NULL
      - provider_id is NULL (any provider) OR matches appointment.provider_id
      - preferred_times is 'any', or matches the time bucket of scheduled_at
      - preferred_days is NULL/empty, or contains the weekday of scheduled_at
    """
    now = datetime.now(timezone.utc)
    if appointment.scheduled_at - now < timedelta(hours=_MIN_LEAD_HOURS):
        return None

    appt_day    = _day_name(appointment.scheduled_at)
    appt_bucket = _time_bucket(appointment.scheduled_at)

    result = await db.execute(
        select(WaitlistEntry)
        .where(
            WaitlistEntry.tenant_id == appointment.tenant_id,
            WaitlistEntry.filled_at.is_(None),
            or_(
                WaitlistEntry.provider_id.is_(None),
                WaitlistEntry.provider_id == appointment.provider_id,
            ),
        )
        .order_by(WaitlistEntry.added_at)   # FIFO: earliest-added gets priority
    )
    entries = list(result.scalars().all())

    for entry in entries:
        if entry.preferred_times != "any" and entry.preferred_times != appt_bucket:
            continue
        if entry.preferred_days and appt_day not in entry.preferred_days:
            continue
        return entry

    return None


async def notify_waitlist_patient(
    db: AsyncSession,
    entry: WaitlistEntry,
    appointment: Appointment,
    twilio_client: Any,
    from_number: str,
) -> Notification:
    """SMS the waitlist patient, mark the entry filled, append audit event.

    All DB mutations are committed in a single transaction so a Twilio failure
    (which raises before the commit) leaves the entry and event unchanged.
    """
    patient = await db.get(Patient, entry.patient_id)
    if patient is None:
        raise WaitlistError(f"Patient {entry.patient_id} not found")

    provider = await db.get(Provider, appointment.provider_id)
    if provider is None:
        raise WaitlistError(f"Provider {appointment.provider_id} not found")

    from app.models.user import User  # local import avoids circular at module level
    provider_user = (
        await db.execute(select(User).where(User.id == provider.user_id))
    ).scalar_one_or_none()
    if provider_user is None:
        raise WaitlistError(f"User {provider.user_id} not found")

    tenant = await db.get(Tenant, appointment.tenant_id)
    if tenant is None:
        raise WaitlistError(f"Tenant {appointment.tenant_id} not found")

    first_name = patient.full_name.split()[0]
    last_name  = provider_user.full_name.split()[-1]
    date_str   = appointment.scheduled_at.strftime("%-b %-d")
    time_str   = appointment.scheduled_at.strftime("%-I:%M %p")

    sms_body = _truncate(
        f"Hi {first_name}, a slot opened with Dr. {last_name} on "
        f"{date_str} at {time_str}. Contact {tenant.name} to book it."
    )

    loop = asyncio.get_running_loop()
    message = await loop.run_in_executor(
        None,
        functools.partial(
            twilio_client.messages.create,
            body=sms_body,
            from_=from_number,
            to=patient.phone,
        ),
    )

    now = datetime.now(timezone.utc)

    notif = Notification(
        tenant_id=appointment.tenant_id,
        appointment_id=appointment.id,
        channel="sms",
        direction="outbound",
        body=sms_body,
        twilio_sid=message.sid,
        status="sent",
        sent_at=now,
    )
    db.add(notif)
    await db.flush()   # populate notif.id before the event references it

    entry.filled_at = now

    db.add(AppointmentEvent(
        tenant_id=appointment.tenant_id,
        appointment_id=appointment.id,
        event_type="waitlist_filled",
        actor_id=None,
        event_metadata={
            "waitlist_entry_id":    str(entry.id),
            "notified_patient_id":  str(entry.patient_id),
            "notification_id":      str(notif.id),
        },
    ))

    await db.commit()
    await db.refresh(notif)
    return notif
