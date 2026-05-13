"""Reschedule service — zero FastAPI imports.

get_available_slots: returns open time slots for a provider within a date window.
reschedule_appointment: validates and applies a patient-initiated reschedule via
    a portal JWT.

Slot grid: 09:00–17:00 UTC, 45-minute intervals.  A slot is available when no
active (scheduled / confirmed / rescheduled) appointment for the same provider
overlaps the [slot_start, slot_start + duration) interval.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TokenError, decode_token
from app.models.appointment import Appointment
from app.models.appointment_event import AppointmentEvent
from app.models.provider import Provider

_SLOT_MINUTES = 45
_DAY_START = 9   # 09:00 UTC
_DAY_END = 17    # 17:00 UTC — last valid slot: 16:15 (16:15+45 = 17:00)

_ACTIVE_STATUSES = frozenset({"scheduled", "confirmed", "rescheduled"})


class RescheduleError(Exception):
    pass


# ── Internal helpers ──────────────────────────────────────────────────────────

def _candidate_slots(from_date: date, days_ahead: int) -> list[datetime]:
    """UTC grid slots for [from_date, from_date + days_ahead)."""
    slots: list[datetime] = []
    for offset in range(days_ahead):
        d = from_date + timedelta(days=offset)
        slot = datetime(d.year, d.month, d.day, _DAY_START, 0, tzinfo=timezone.utc)
        day_end = datetime(d.year, d.month, d.day, _DAY_END, 0, tzinfo=timezone.utc)
        while slot + timedelta(minutes=_SLOT_MINUTES) <= day_end:
            slots.append(slot)
            slot += timedelta(minutes=_SLOT_MINUTES)
    return slots


def _overlap(
    a_start: datetime, a_duration: int,
    b_start: datetime, b_duration: int,
) -> bool:
    return a_start < b_start + timedelta(minutes=b_duration) and \
           b_start < a_start + timedelta(minutes=a_duration)


# ── Public service functions ──────────────────────────────────────────────────

async def get_available_slots(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    provider_id: uuid.UUID,
    from_date: date,
    days_ahead: int = 7,
) -> list[datetime]:
    """Return open 45-min slot datetimes for a provider within the window.

    Raises RescheduleError if the provider is not found or inactive.
    """
    provider = (
        await db.execute(
            select(Provider).where(
                Provider.id == provider_id,
                Provider.tenant_id == tenant_id,
                Provider.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if provider is None:
        raise RescheduleError(f"Provider {provider_id} not found")

    window_start = datetime(from_date.year, from_date.month, from_date.day, tzinfo=timezone.utc)
    window_end = window_start + timedelta(days=days_ahead)

    # Fetch slightly wider than the window: an appointment starting up to 480 min
    # before the window can still overlap the first slot.
    booked = (
        await db.execute(
            select(Appointment).where(
                Appointment.tenant_id == tenant_id,
                Appointment.provider_id == provider_id,
                Appointment.status.in_(list(_ACTIVE_STATUSES)),
                Appointment.scheduled_at >= window_start - timedelta(minutes=480),
                Appointment.scheduled_at < window_end,
            )
        )
    ).scalars().all()

    now = datetime.now(timezone.utc)
    available: list[datetime] = []
    for slot in _candidate_slots(from_date, days_ahead):
        if slot <= now:
            continue
        if not any(_overlap(slot, _SLOT_MINUTES, b.scheduled_at, b.duration_minutes) for b in booked):
            available.append(slot)

    return available


async def reschedule_appointment(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    appointment_id: uuid.UUID,
    new_slot: datetime,
    patient_jwt: str,
) -> Appointment:
    """Apply a patient-initiated reschedule.

    Decodes the portal JWT to verify the token authorises this exact appointment
    and tenant.  Checks availability excluding the appointment's current slot,
    then transitions to 'rescheduled' and appends an audit event.

    Raises RescheduleError on any validation failure.
    """
    try:
        payload = decode_token(patient_jwt, "portal")
    except TokenError as exc:
        raise RescheduleError(f"Invalid portal token: {exc}") from exc

    jwt_patient_id    = uuid.UUID(payload["sub"])
    jwt_appointment_id = uuid.UUID(payload["appointment_id"])
    jwt_tenant_id     = uuid.UUID(payload["tenant_id"])

    if jwt_tenant_id != tenant_id:
        raise RescheduleError("Tenant mismatch in portal token")
    if jwt_appointment_id != appointment_id:
        raise RescheduleError("Token does not authorise this appointment")

    appt = (
        await db.execute(
            select(Appointment).where(
                Appointment.id == appointment_id,
                Appointment.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if appt is None:
        raise RescheduleError(f"Appointment {appointment_id} not found")

    if appt.patient_id != jwt_patient_id:
        raise RescheduleError("Token does not authorise this appointment")

    if appt.status not in _ACTIVE_STATUSES:
        raise RescheduleError(
            f"Cannot reschedule appointment with status '{appt.status}'"
        )

    if new_slot <= datetime.now(timezone.utc):
        raise RescheduleError("New slot must be in the future")

    # Conflict check — exclude the appointment itself so its current slot is free
    conflicting = (
        await db.execute(
            select(Appointment).where(
                Appointment.tenant_id == tenant_id,
                Appointment.provider_id == appt.provider_id,
                Appointment.status.in_(list(_ACTIVE_STATUSES)),
                Appointment.id != appointment_id,
                Appointment.scheduled_at >= new_slot - timedelta(minutes=480),
                Appointment.scheduled_at < new_slot + timedelta(minutes=appt.duration_minutes),
            )
        )
    ).scalars().all()

    if any(_overlap(new_slot, appt.duration_minutes, c.scheduled_at, c.duration_minutes)
           for c in conflicting):
        raise RescheduleError(f"Slot {new_slot.isoformat()} is not available")

    old_scheduled_at = appt.scheduled_at
    appt.scheduled_at = new_slot
    appt.status = "rescheduled"

    db.add(AppointmentEvent(
        tenant_id=tenant_id,
        appointment_id=appt.id,
        event_type="rescheduled",
        actor_id=None,  # patient action — no staff user_id
        event_metadata={
            "old_scheduled_at": old_scheduled_at.isoformat(),
            "new_scheduled_at": new_slot.isoformat(),
            "via": "patient_portal",
        },
    ))

    await db.commit()
    await db.refresh(appt)
    return appt
