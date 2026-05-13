"""Patient service — zero FastAPI imports.

Raises NotFoundError; the route layer maps it to HTTPException 404.
RLS is enforced at the DB level (set by get_current_tenant dependency before
any service call). All queries also filter explicitly by tenant_id as a
belt-and-suspenders safeguard.
"""
import uuid
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.schemas.patient import PatientCreate, PatientUpdate


class NotFoundError(Exception):
    pass


async def list_patients(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Patient], int]:
    """Return a page of patients and the total matching count."""
    base = select(Patient).where(Patient.tenant_id == tenant_id)

    if search:
        pattern = f"%{search}%"
        base = base.where(
            or_(
                Patient.full_name.ilike(pattern),
                Patient.phone.ilike(pattern),
            )
        )

    total_result = await db.execute(
        select(func.count()).select_from(base.subquery())
    )
    total = total_result.scalar_one()

    rows_result = await db.execute(
        base.order_by(Patient.full_name)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    patients = list(rows_result.scalars().all())

    return patients, total


async def get_patient(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    patient_id: uuid.UUID,
) -> Patient:
    """Fetch a single patient. Raises NotFoundError if not found or wrong tenant."""
    result = await db.execute(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.tenant_id == tenant_id,
        )
    )
    patient = result.scalar_one_or_none()
    if patient is None:
        raise NotFoundError(f"Patient {patient_id} not found")
    return patient


async def create_patient(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    data: PatientCreate,
) -> Patient:
    """Insert a new patient row and return the persisted object."""
    patient = Patient(
        tenant_id=tenant_id,
        full_name=data.full_name,
        phone=data.phone,
        email=str(data.email) if data.email else None,
        dob=data.dob,
        notes=data.notes,
    )
    db.add(patient)
    await db.commit()
    await db.refresh(patient)
    return patient


async def update_patient(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    patient_id: uuid.UUID,
    data: PatientUpdate,
) -> Patient:
    """Apply a partial update. Only non-None fields are written."""
    patient = await get_patient(db, tenant_id, patient_id)

    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        # EmailStr serialises as a string; store as plain str
        if field == "email" and value is not None:
            value = str(value)
        setattr(patient, field, value)

    await db.commit()
    await db.refresh(patient)
    return patient
