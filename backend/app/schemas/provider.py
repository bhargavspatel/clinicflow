import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ProviderCreate(BaseModel):
    user_id: uuid.UUID
    specialty: str = Field(min_length=1, max_length=200)
    is_active: bool = True


class ProviderUpdate(BaseModel):
    specialty: Optional[str] = Field(default=None, min_length=1, max_length=200)
    is_active: Optional[bool] = None


class ProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    specialty: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
