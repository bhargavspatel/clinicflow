import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AppointmentPortalOut(BaseModel):
    """Appointment details returned to the patient portal."""
    id: uuid.UUID
    scheduled_at: datetime
    appointment_type: str
    status: str
    duration_minutes: int
    patient_name: str
    provider_specialty: str
    provider_id: uuid.UUID


class SlotsResponse(BaseModel):
    provider_id: uuid.UUID
    slots: list[datetime]


class RescheduleRequest(BaseModel):
    appointment_id: uuid.UUID
    new_slot: datetime


class RescheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    provider_id: uuid.UUID
    tenant_id: uuid.UUID
    scheduled_at: datetime
    duration_minutes: int
    appointment_type: str
    status: str
