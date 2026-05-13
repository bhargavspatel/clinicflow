"""Patient portal endpoints.

All routes require a portal JWT (type='portal') issued by GET /auth/magic-link/verify.
The JWT binds a specific patient to a specific appointment — patients can only
act on the appointment their magic link was issued for.
"""
import uuid
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_portal_payload
from app.models.appointment import Appointment
from app.models.patient import Patient
from app.models.provider import Provider
from app.schemas.portal import AppointmentPortalOut, RescheduleOut, RescheduleRequest, SlotsResponse
from app.services import reschedule_service

router = APIRouter()


@router.get("/appointment", response_model=AppointmentPortalOut)
async def get_portal_appointment(
    db: AsyncSession = Depends(get_db),
    portal_payload: dict[str, Any] = Depends(get_portal_payload),
) -> AppointmentPortalOut:
    """Return the appointment bound to the portal JWT, with patient and provider names."""
    appointment_id = uuid.UUID(portal_payload["appointment_id"])
    tenant_id      = uuid.UUID(portal_payload["tenant_id"])
    patient_id     = uuid.UUID(portal_payload["patient_id"])

    appt = await db.get(Appointment, appointment_id)
    if appt is None or appt.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")

    patient  = await db.get(Patient, patient_id)
    provider = await db.get(Provider, appt.provider_id)

    return AppointmentPortalOut(
        id=appt.id,
        scheduled_at=appt.scheduled_at,
        appointment_type=appt.appointment_type,
        status=appt.status,
        duration_minutes=appt.duration_minutes,
        patient_name=patient.full_name if patient else "Patient",
        provider_specialty=provider.specialty if provider else "Provider",
        provider_id=appt.provider_id,
    )


@router.get("/slots", response_model=SlotsResponse)
async def get_slots(
    provider_id: uuid.UUID,
    from_date: Optional[date] = Query(default=None),
    days_ahead: int = Query(default=7, ge=1, le=30),
    db: AsyncSession = Depends(get_db),
    portal_payload: dict[str, Any] = Depends(get_portal_payload),
) -> SlotsResponse:
    """Return available 45-min slots for a provider.

    from_date defaults to today when omitted.
    """
    if from_date is None:
        from_date = date.today()

    tenant_id = uuid.UUID(portal_payload["tenant_id"])

    try:
        slots = await reschedule_service.get_available_slots(
            db, tenant_id, provider_id, from_date, days_ahead
        )
    except reschedule_service.RescheduleError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return SlotsResponse(provider_id=provider_id, slots=slots)


@router.post("/reschedule", response_model=RescheduleOut)
async def reschedule(
    data: RescheduleRequest,
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
    portal_payload: dict[str, Any] = Depends(get_portal_payload),
) -> RescheduleOut:
    """Reschedule the appointment bound to the portal JWT to a new slot.

    The portal JWT is re-decoded inside the service to verify the token
    authorises exactly this appointment_id.
    """
    raw_token = (
        authorization[7:]
        if authorization and authorization.startswith("Bearer ")
        else ""
    )
    tenant_id = uuid.UUID(portal_payload["tenant_id"])

    try:
        appt = await reschedule_service.reschedule_appointment(
            db=db,
            tenant_id=tenant_id,
            appointment_id=data.appointment_id,
            new_slot=data.new_slot,
            patient_jwt=raw_token,
        )
    except reschedule_service.RescheduleError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return appt
