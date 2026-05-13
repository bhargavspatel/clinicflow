import re
import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


def _validate_e164(v: str) -> str:
    if not _E164_RE.match(v):
        raise ValueError("phone must be E.164 format, e.g. +12125551234")
    return v


class PatientCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    phone: str
    email: Optional[EmailStr] = None
    dob: Optional[date] = None
    notes: Optional[str] = None

    @field_validator("phone")
    @classmethod
    def phone_e164(cls, v: str) -> str:
        return _validate_e164(v)


class PatientUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    dob: Optional[date] = None
    notes: Optional[str] = None

    @field_validator("phone")
    @classmethod
    def phone_e164(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return _validate_e164(v)
        return v


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    full_name: str
    phone: str
    email: Optional[str]
    dob: Optional[date]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime


class PatientListOut(BaseModel):
    items: list[PatientOut]
    total: int
    page: int
    page_size: int
