from enum import Enum
from typing import Optional

from sqlalchemy import Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class PlanTier(str, Enum):
    starter = "starter"
    growth = "growth"
    pro = "pro"


class Tenant(TimestampMixin, Base):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    subdomain: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    plan_tier: Mapped[str] = mapped_column(
        Text, nullable=False, default=PlanTier.starter.value
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
