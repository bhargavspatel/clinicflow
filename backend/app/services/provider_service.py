"""Provider service — zero FastAPI imports.

Raises NotFoundError / ConflictError; the route layer maps them to HTTP exceptions.
RLS is enforced at the DB level; all queries also filter by tenant_id explicitly.
"""
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider import Provider
from app.schemas.provider import ProviderCreate, ProviderUpdate


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    pass


async def list_providers(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    is_active: Optional[bool] = None,
) -> list[Provider]:
    q = select(Provider).where(Provider.tenant_id == tenant_id)
    if is_active is not None:
        q = q.where(Provider.is_active == is_active)
    result = await db.execute(q.order_by(Provider.specialty))
    return list(result.scalars().all())


async def get_provider(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    provider_id: uuid.UUID,
) -> Provider:
    result = await db.execute(
        select(Provider).where(
            Provider.id == provider_id,
            Provider.tenant_id == tenant_id,
        )
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise NotFoundError(f"Provider {provider_id} not found")
    return provider


async def create_provider(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    data: ProviderCreate,
) -> Provider:
    existing = await db.execute(
        select(Provider).where(
            Provider.user_id == data.user_id,
            Provider.tenant_id == tenant_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(f"User {data.user_id} is already a provider in this tenant")

    provider = Provider(
        tenant_id=tenant_id,
        user_id=data.user_id,
        specialty=data.specialty,
        is_active=data.is_active,
    )
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    return provider


async def update_provider(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    provider_id: uuid.UUID,
    data: ProviderUpdate,
) -> Provider:
    provider = await get_provider(db, tenant_id, provider_id)

    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(provider, field, value)

    await db.commit()
    await db.refresh(provider)
    return provider
