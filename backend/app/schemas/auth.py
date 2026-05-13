import re
import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# Subdomain rules: lowercase alphanumeric + hyphens, 3-63 chars,
# must start and end with alphanumeric (same constraints as DNS labels).
_SUBDOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{1,61}[a-z0-9]$")


class RegisterRequest(BaseModel):
    clinic_name: str = Field(min_length=2, max_length=100)
    subdomain: str = Field(min_length=3, max_length=63)
    owner_full_name: str = Field(min_length=2, max_length=100)
    owner_email: EmailStr
    password: str = Field(min_length=8)

    @field_validator("subdomain")
    @classmethod
    def subdomain_must_be_slug(cls, v: str) -> str:
        if not _SUBDOMAIN_RE.match(v):
            raise ValueError(
                "subdomain must be 3-63 lowercase alphanumeric characters or hyphens, "
                "and must not start or end with a hyphen"
            )
        return v


class LoginRequest(BaseModel):
    subdomain: str = Field(min_length=3, max_length=63)
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until the access token expires


class RefreshRequest(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    role: str
    full_name: str
    is_active: bool


class MagicLinkSendRequest(BaseModel):
    appointment_id: uuid.UUID


class PortalTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 1800
    patient_id: uuid.UUID
    appointment_id: uuid.UUID
