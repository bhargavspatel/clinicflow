import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from twilio.rest import Client as TwilioClient

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.redis import get_redis
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    MagicLinkSendRequest,
    PortalTokenResponse,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from app.services import magic_link_service
from app.services.auth_service import AuthError, ConflictError, login, logout, refresh, register

router = APIRouter()


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register_tenant(
    data: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    try:
        return await register(db, data)
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/login", response_model=TokenResponse)
async def login_user(
    data: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    # Resolve subdomain → tenant before delegating to the service.
    # Tenants table has no RLS — this query is always safe without a JWT context.
    result = await db.execute(
        select(Tenant).where(Tenant.subdomain == data.subdomain)
    )
    tenant = result.scalar_one_or_none()

    # Treat missing/inactive tenant identically to bad credentials to avoid
    # leaking whether a given subdomain exists.
    if tenant is None or not tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    try:
        return await login(db, str(data.email), data.password, tenant.id)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(
    data: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    try:
        return await refresh(db, data.refresh_token)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout_user(
    data: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> None:
    try:
        await logout(db, data.refresh_token)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc


@router.post("/magic-link/send", status_code=status.HTTP_204_NO_CONTENT)
async def send_magic_link(
    data: MagicLinkSendRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    redis=Depends(get_redis),
) -> None:
    settings = get_settings()
    twilio_client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)
    try:
        await magic_link_service.send_magic_link(
            db=db,
            appointment_id=data.appointment_id,
            tenant_id=current_user.tenant_id,
            redis=redis,
            twilio_client=twilio_client,
            from_number=settings.twilio_from_number,
        )
    except magic_link_service.MagicLinkError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/magic-link/verify", response_model=PortalTokenResponse)
async def verify_magic_link(
    token: str = Query(..., min_length=1),
    redis=Depends(get_redis),
) -> PortalTokenResponse:
    try:
        data = await magic_link_service.verify_magic_link(token, redis)
    except magic_link_service.MagicLinkError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    patient_id    = uuid.UUID(data["patient_id"])
    appointment_id = uuid.UUID(data["appointment_id"])
    tenant_id     = uuid.UUID(data["tenant_id"])

    portal_token = magic_link_service.generate_patient_jwt(patient_id, appointment_id, tenant_id)

    return PortalTokenResponse(
        access_token=portal_token,
        patient_id=patient_id,
        appointment_id=appointment_id,
    )
