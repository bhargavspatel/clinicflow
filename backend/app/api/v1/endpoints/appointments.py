import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.arq_pool import get_arq_pool
from app.core.database import get_db
from app.core.deps import get_current_tenant, get_current_user
from app.core.redis import get_redis
from app.core.ws_events import broadcast
from app.models.user import User
from app.schemas.appointment import (
    AppointmentCreate,
    AppointmentListOut,
    AppointmentOut,
    AppointmentStatus,
    AppointmentUpdate,
)
from app.services.appointment_service import (
    InvalidTransitionError,
    NotFoundError,
    create_appointment,
    get_appointment,
    list_appointments,
    score_appointment,
    update_appointment,
)

router = APIRouter()


@router.get("", response_model=AppointmentListOut)
async def list_appointments_endpoint(
    date_from: Optional[datetime] = Query(default=None),
    date_to: Optional[datetime] = Query(default=None),
    status: Optional[AppointmentStatus] = Query(default=None),
    provider_id: Optional[uuid.UUID] = Query(default=None),
    risk_bucket: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AppointmentListOut:
    appointments, total = await list_appointments(
        db,
        tenant_id,
        date_from=date_from,
        date_to=date_to,
        status=status,
        provider_id=provider_id,
        risk_bucket=risk_bucket,
        page=page,
        page_size=page_size,
    )
    return AppointmentListOut(
        items=appointments, total=total, page=page, page_size=page_size
    )


@router.post("", response_model=AppointmentOut, status_code=status.HTTP_201_CREATED)
async def create_appointment_endpoint(
    data: AppointmentCreate,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AppointmentOut:
    return await create_appointment(db, tenant_id, data, actor_id=current_user.id)


@router.post("/{appointment_id}/score", response_model=AppointmentOut)
async def score_appointment_endpoint(
    appointment_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AppointmentOut:
    try:
        return await score_appointment(db, tenant_id, appointment_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{appointment_id}", response_model=AppointmentOut)
async def get_appointment_endpoint(
    appointment_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AppointmentOut:
    try:
        return await get_appointment(db, tenant_id, appointment_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/{appointment_id}", response_model=AppointmentOut)
async def update_appointment_endpoint(
    appointment_id: uuid.UUID,
    data: AppointmentUpdate,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    arq=Depends(get_arq_pool),
    redis=Depends(get_redis),
) -> AppointmentOut:
    try:
        result = await update_appointment(
            db, tenant_id, appointment_id, data, actor_id=current_user.id
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    if data.status == "cancelled":
        await arq.enqueue_job("fill_waitlist_task", appointment_id)

    if data.status is not None:
        await broadcast(
            redis,
            str(tenant_id),
            "appointment.status_changed",
            {
                "appointment_id": str(result.id),
                "patient_id":     str(result.patient_id),
                "provider_id":    str(result.provider_id),
                "status":         result.status,
                "scheduled_at":   result.scheduled_at.isoformat(),
            },
        )

    return result
