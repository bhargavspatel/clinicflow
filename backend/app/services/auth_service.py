"""Auth service — zero FastAPI imports.

Raises AuthError or ConflictError; the route layer maps these to HTTPException.
"""
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt as _bcrypt
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.auth import RegisterRequest, TokenResponse

# Pre-computed dummy hash used in login's constant-time branch (rounds=4 for
# negligible import cost). Prevents email-enumeration via timing differences.
_DUMMY_HASH: str = _bcrypt.hashpw(b"dummy-constant-time", _bcrypt.gensalt(4)).decode()


# ── Domain exceptions ─────────────────────────────────────────────────────────

class AuthError(Exception):
    """Wrong credentials, inactive user, or invalid/revoked token."""


class ConflictError(Exception):
    """Subdomain or email already exists."""


# ── Private helpers ───────────────────────────────────────────────────────────

def _build_token_pair(user: User) -> tuple[TokenResponse, str]:
    """Return (TokenResponse, raw_refresh_token). Caller persists the token."""
    settings = get_settings()
    access = create_access_token(user.id, user.tenant_id, user.role)
    raw_refresh = create_refresh_token(user.id, user.tenant_id)
    response = TokenResponse(
        access_token=access,
        refresh_token=raw_refresh,
        expires_in=settings.access_token_expire_minutes * 60,
    )
    return response, raw_refresh


async def _persist_refresh_token(
    db: AsyncSession, user: User, raw_token: str
) -> None:
    settings = get_settings()
    expires_at = datetime.now(timezone.utc) + timedelta(
        days=settings.refresh_token_expire_days
    )
    db.add(
        RefreshToken(
            user_id=user.id,
            tenant_id=user.tenant_id,
            token_hash=hash_refresh_token(raw_token),
            expires_at=expires_at,
        )
    )


async def _set_rls(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    # set_config(..., true) = SET LOCAL — transaction-scoped, pool-safe.
    # SET LOCAL itself doesn't accept bind parameters in PostgreSQL.
    await db.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )


# ── Public API ────────────────────────────────────────────────────────────────

async def register(db: AsyncSession, data: RegisterRequest) -> TokenResponse:
    """Create a new tenant + owner user in one transaction, return token pair."""
    # Subdomain uniqueness check before we touch anything else
    existing = await db.execute(
        select(Tenant).where(Tenant.subdomain == data.subdomain)
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(f"Subdomain '{data.subdomain}' is already taken")

    tenant = Tenant(
        name=data.clinic_name,
        subdomain=data.subdomain,
    )
    db.add(tenant)
    # flush assigns tenant.id (Python-side UUID) and writes the row so the FK
    # on users.tenant_id can resolve within the same transaction
    await db.flush()

    await _set_rls(db, tenant.id)

    user = User(
        tenant_id=tenant.id,
        email=str(data.owner_email),
        password_hash=hash_password(data.password),
        role="owner",
        full_name=data.owner_full_name,
    )
    db.add(user)
    await db.flush()

    token_response, raw_refresh = _build_token_pair(user)
    await _persist_refresh_token(db, user, raw_refresh)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(
            "Registration failed — subdomain or email already exists"
        ) from exc

    return token_response


async def login(
    db: AsyncSession,
    email: str,
    password: str,
    tenant_id: uuid.UUID,
) -> TokenResponse:
    """Validate credentials and return a fresh token pair."""
    await _set_rls(db, tenant_id)

    result = await db.execute(
        select(User).where(User.tenant_id == tenant_id, User.email == email)
    )
    user = result.scalar_one_or_none()

    # Always call verify_password to keep timing constant regardless of whether
    # the user exists — prevents email enumeration via response-time oracle.
    candidate_hash = user.password_hash if user is not None else _DUMMY_HASH
    password_ok = verify_password(password, candidate_hash)

    if user is None or not password_ok or not user.is_active:
        raise AuthError("Invalid credentials")

    token_response, raw_refresh = _build_token_pair(user)
    await _persist_refresh_token(db, user, raw_refresh)
    await db.commit()
    return token_response


async def refresh(db: AsyncSession, raw_refresh_token: str) -> TokenResponse:
    """Validate, revoke, and rotate a refresh token; return a new token pair."""
    try:
        payload = decode_token(raw_refresh_token, "refresh")
    except TokenError as exc:
        raise AuthError(str(exc)) from exc

    tenant_id = uuid.UUID(payload["tenant_id"])
    user_id = uuid.UUID(payload["sub"])

    await _set_rls(db, tenant_id)

    token_hash = hash_refresh_token(raw_refresh_token)
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.user_id == user_id,
            RefreshToken.tenant_id == tenant_id,
        )
    )
    stored = result.scalar_one_or_none()

    if stored is None or stored.revoked_at is not None or stored.expires_at <= now:
        raise AuthError("Refresh token is invalid, expired, or revoked")

    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise AuthError("User not found or inactive")

    # Revoke old token then issue new pair atomically
    stored.revoked_at = now
    token_response, raw_new_refresh = _build_token_pair(user)
    await _persist_refresh_token(db, user, raw_new_refresh)
    await db.commit()
    return token_response


async def logout(db: AsyncSession, raw_refresh_token: str) -> None:
    """Revoke a refresh token. Raises AuthError if not found or already revoked."""
    try:
        payload = decode_token(raw_refresh_token, "refresh")
    except TokenError as exc:
        raise AuthError(str(exc)) from exc

    tenant_id = uuid.UUID(payload["tenant_id"])
    user_id = uuid.UUID(payload["sub"])

    await _set_rls(db, tenant_id)

    token_hash = hash_refresh_token(raw_refresh_token)
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.user_id == user_id,
            RefreshToken.tenant_id == tenant_id,
        )
    )
    stored = result.scalar_one_or_none()

    if stored is None or stored.revoked_at is not None:
        raise AuthError("Token not found or already revoked")

    stored.revoked_at = datetime.now(timezone.utc)
    await db.commit()
