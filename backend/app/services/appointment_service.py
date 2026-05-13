"""Appointment service — zero FastAPI imports.

Status transitions are validated against an explicit DAG.
Every state change appends a row to appointment_events (audit trail).
Raises NotFoundError / InvalidTransitionError; the route layer maps them to HTTP exceptions.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.appointment_event import AppointmentEvent
from app.models.patient import Patient
from app.schemas.appointment import AppointmentCreate, AppointmentUpdate
from app.services.risk_scoring_service import (
    AppointmentInput,
    PatientHistory,
    PastAppointment,
    ScoringContext,
    calculate_risk,
)

# Terminal states appear with empty sets — no outbound transitions allowed.
_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "scheduled":   frozenset({"confirmed", "rescheduled", "cancelled", "no_show"}),
    "confirmed":   frozenset({"rescheduled", "completed", "no_show", "cancelled"}),
    "rescheduled": frozenset({"confirmed", "cancelled", "no_show"}),
    "completed":   frozenset(),
    "no_show":     frozenset(),
    "cancelled":   frozenset(),
}


class NotFoundError(Exception):
    pass


class InvalidTransitionError(Exception):
    pass


async def list_appointments(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    status: Optional[str] = None,
    provider_id: Optional[uuid.UUID] = None,
    risk_bucket: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Appointment], int]:
    base = select(Appointment).where(Appointment.tenant_id == tenant_id)

    if date_from is not None:
        base = base.where(Appointment.scheduled_at >= date_from)
    if date_to is not None:
        base = base.where(Appointment.scheduled_at <= date_to)
    if status is not None:
        base = base.where(Appointment.status == status)
    if provider_id is not None:
        base = base.where(Appointment.provider_id == provider_id)
    if risk_bucket is not None:
        base = base.where(Appointment.risk_bucket == risk_bucket)

    total_result = await db.execute(
        select(func.count()).select_from(base.subquery())
    )
    total = total_result.scalar_one()

    rows_result = await db.execute(
        base.order_by(Appointment.scheduled_at)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(rows_result.scalars().all()), total


async def get_appointment(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    appointment_id: uuid.UUID,
) -> Appointment:
    result = await db.execute(
        select(Appointment).where(
            Appointment.id == appointment_id,
            Appointment.tenant_id == tenant_id,
        )
    )
    appt = result.scalar_one_or_none()
    if appt is None:
        raise NotFoundError(f"Appointment {appointment_id} not found")
    return appt


async def create_appointment(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    data: AppointmentCreate,
    actor_id: Optional[uuid.UUID] = None,
) -> Appointment:
    appt = Appointment(
        tenant_id=tenant_id,
        patient_id=data.patient_id,
        provider_id=data.provider_id,
        scheduled_at=data.scheduled_at,
        duration_minutes=data.duration_minutes,
        appointment_type=data.appointment_type,
        status=data.status,
        notes=data.notes,
    )
    db.add(appt)
    await db.flush()  # populate appt.id before the event references it

    db.add(AppointmentEvent(
        tenant_id=tenant_id,
        appointment_id=appt.id,
        event_type="created",
        actor_id=actor_id,
    ))

    await db.commit()
    await db.refresh(appt)
    return appt


async def update_appointment(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    appointment_id: uuid.UUID,
    data: AppointmentUpdate,
    actor_id: Optional[uuid.UUID] = None,
) -> Appointment:
    appt = await get_appointment(db, tenant_id, appointment_id)

    updates = data.model_dump(exclude_unset=True)

    new_status = updates.get("status")
    if new_status is not None and new_status != appt.status:
        allowed = _VALID_TRANSITIONS.get(appt.status, frozenset())
        if new_status not in allowed:
            raise InvalidTransitionError(
                f"Cannot transition '{appt.status}' → '{new_status}'"
            )
        db.add(AppointmentEvent(
            tenant_id=tenant_id,
            appointment_id=appt.id,
            event_type=new_status,
            actor_id=actor_id,
        ))

    for field, value in updates.items():
        setattr(appt, field, value)

    await db.commit()
    await db.refresh(appt)
    return appt


async def score_appointment(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    appointment_id: uuid.UUID,
) -> Appointment:
    """Score an appointment using the risk rubric and persist the result.

    Fetches the appointment, its patient, and all prior appointments for that
    patient, then calls the pure calculate_risk() function.  Writes
    risk_score / risk_bucket / last_scored_at onto the appointment row and
    appends a 'scored' event with the full breakdown in event_metadata.
    """
    appt = await get_appointment(db, tenant_id, appointment_id)

    # Fetch the patient to determine has_phone
    patient_result = await db.execute(
        select(Patient).where(
            Patient.id == appt.patient_id,
            Patient.tenant_id == tenant_id,
        )
    )
    patient = patient_result.scalar_one_or_none()
    if patient is None:
        raise NotFoundError(f"Patient {appt.patient_id} not found")

    # Fetch all prior appointments for this patient (excluding the one being scored)
    prior_result = await db.execute(
        select(Appointment).where(
            Appointment.patient_id == appt.patient_id,
            Appointment.tenant_id == tenant_id,
            Appointment.id != appointment_id,
        )
    )
    prior_appts = list(prior_result.scalars().all())

    # Build PatientHistory — use updated_at as proxy for completed_at since the
    # Appointment model has no dedicated completed_at column; updated_at is set by
    # onupdate=_utcnow whenever the row changes, so it closely tracks completion time.
    past = [
        PastAppointment(
            scheduled_at=a.scheduled_at,
            status=a.status,
            completed_at=a.updated_at if a.status == "completed" else None,
        )
        for a in prior_appts
    ]
    history = PatientHistory(
        past_appointments=past,
        lifetime_completed=sum(1 for a in prior_appts if a.status == "completed"),
        lifetime_no_shows=sum(1 for a in prior_appts if a.status == "no_show"),
        has_phone=bool(patient.phone),
    )

    now = datetime.now(timezone.utc)
    # TODO: integrate a weather-alert service before Phase 5 goes live
    context = ScoringContext(now=now, weather_alert=False)

    appt_input = AppointmentInput(
        scheduled_at=appt.scheduled_at,
        appointment_type=appt.appointment_type,
        duration_minutes=appt.duration_minutes,
    )

    result = calculate_risk(appt_input, history, context)

    appt.risk_score = result.score
    appt.risk_bucket = result.bucket
    appt.last_scored_at = result.scored_at

    db.add(AppointmentEvent(
        tenant_id=tenant_id,
        appointment_id=appt.id,
        event_type="scored",
        actor_id=None,  # system-generated; no human actor
        event_metadata={
            "risk_score": result.score,
            "bucket": result.bucket,
            "factors": result.factors,
        },
    ))

    await db.commit()
    await db.refresh(appt)
    return appt
