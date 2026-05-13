import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_tenant, get_current_user
from app.models.user import User
from app.schemas.provider import ProviderCreate, ProviderOut, ProviderUpdate
from app.services.provider_service import (
    ConflictError,
    NotFoundError,
    create_provider,
    get_provider,
    list_providers,
    update_provider,
)

router = APIRouter()


@router.get("", response_model=list[ProviderOut])
async def list_providers_endpoint(
    is_active: Optional[bool] = Query(default=None),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ProviderOut]:
    return await list_providers(db, tenant_id, is_active=is_active)


@router.post("", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
async def create_provider_endpoint(
    data: ProviderCreate,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProviderOut:
    try:
        return await create_provider(db, tenant_id, data)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/{provider_id}", response_model=ProviderOut)
async def get_provider_endpoint(
    provider_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProviderOut:
    try:
        return await get_provider(db, tenant_id, provider_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/{provider_id}", response_model=ProviderOut)
async def update_provider_endpoint(
    provider_id: uuid.UUID,
    data: ProviderUpdate,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProviderOut:
    try:
        return await update_provider(db, tenant_id, provider_id, data)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
