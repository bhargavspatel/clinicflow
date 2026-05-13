import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_tenant, get_current_user
from app.models.user import User
from app.schemas.patient import PatientCreate, PatientListOut, PatientOut, PatientUpdate
from app.services.patient_service import (
    NotFoundError,
    create_patient,
    get_patient,
    list_patients,
    update_patient,
)

router = APIRouter()


@router.get("", response_model=PatientListOut)
async def list_patients_endpoint(
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PatientListOut:
    patients, total = await list_patients(
        db, tenant_id, search=search, page=page, page_size=page_size
    )
    return PatientListOut(items=patients, total=total, page=page, page_size=page_size)


@router.post("", response_model=PatientOut, status_code=status.HTTP_201_CREATED)
async def create_patient_endpoint(
    data: PatientCreate,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PatientOut:
    return await create_patient(db, tenant_id, data)


@router.get("/{patient_id}", response_model=PatientOut)
async def get_patient_endpoint(
    patient_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PatientOut:
    try:
        return await get_patient(db, tenant_id, patient_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/{patient_id}", response_model=PatientOut)
async def update_patient_endpoint(
    patient_id: uuid.UUID,
    data: PatientUpdate,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PatientOut:
    try:
        return await update_patient(db, tenant_id, patient_id, data)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
