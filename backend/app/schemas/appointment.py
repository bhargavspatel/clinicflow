import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

AppointmentType = Literal["initial_eval", "follow_up", "consultation"]
AppointmentStatus = Literal[
    "scheduled", "confirmed", "rescheduled", "completed", "no_show", "cancelled"
]


class AppointmentCreate(BaseModel):
    patient_id: uuid.UUID
    provider_id: uuid.UUID
    scheduled_at: datetime
    duration_minutes: int = Field(default=45, gt=0, le=480)
    appointment_type: AppointmentType
    status: AppointmentStatus = "scheduled"
    notes: Optional[str] = None


class AppointmentUpdate(BaseModel):
    scheduled_at: Optional[datetime] = None
    duration_minutes: Optional[int] = Field(default=None, gt=0, le=480)
    appointment_type: Optional[AppointmentType] = None
    status: Optional[AppointmentStatus] = None
    notes: Optional[str] = None


class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    patient_id: uuid.UUID
    provider_id: uuid.UUID
    scheduled_at: datetime
    duration_minutes: int
    appointment_type: str
    status: str
    risk_score: Optional[int]
    risk_bucket: Optional[str]
    last_scored_at: Optional[datetime]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime


class AppointmentListOut(BaseModel):
    items: list[AppointmentOut]
    total: int
    page: int
    page_size: int
