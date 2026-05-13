import uuid
from typing import Any, Callable

import sentry_sdk
import structlog
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import TokenError, decode_token
from app.models.user import User


# ── Token extraction ──────────────────────────────────────────────────────────

def _get_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    return authorization[7:]


async def _get_token_payload(token: str = Depends(_get_token)) -> dict[str, Any]:
    try:
        return decode_token(token, "access")
    except TokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


# ── Tenant RLS ────────────────────────────────────────────────────────────────

async def get_current_tenant(
    payload: dict[str, Any] = Depends(_get_token_payload),
    db: AsyncSession = Depends(get_db),
) -> uuid.UUID:
    """Extract tenant_id from JWT and set Postgres RLS context for this session."""
    tenant_id = uuid.UUID(payload["tenant_id"])
    # set_config(..., true) is the functional equivalent of SET LOCAL —
    # transaction-scoped and safe with connection pooling.
    # SET LOCAL does not support bind parameters in PostgreSQL; set_config does.
    await db.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )
    sentry_sdk.set_tag("tenant_id", str(tenant_id))
    structlog.contextvars.bind_contextvars(tenant_id=str(tenant_id))
    return tenant_id


# ── Current user ──────────────────────────────────────────────────────────────

async def get_current_user(
    payload: dict[str, Any] = Depends(_get_token_payload),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Validate JWT, set RLS, fetch and return the authenticated User row."""
    user_id = uuid.UUID(payload["sub"])
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    # Defence-in-depth: JWT tenant claim must match the DB row (RLS already ensures this)
    if user.tenant_id != tenant_id:
        raise HTTPException(status_code=401, detail="Tenant mismatch")

    structlog.contextvars.bind_contextvars(user_id=str(user.id))
    return user


# ── Role-based access ─────────────────────────────────────────────────────────

def _require_roles(*roles: str) -> Callable[..., Any]:
    async def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return current_user
    return dependency


async def get_portal_payload(
    token: str = Depends(_get_token),
) -> dict[str, Any]:
    """Decode a portal JWT (type='portal') for patient-facing /portal/* endpoints."""
    try:
        return decode_token(token, "portal")
    except TokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


# Owner only — billing, user management
require_owner = _require_roles("owner")

# Provider and above — appointment access, patient records
require_provider_or_above = _require_roles("owner", "provider")

# Front desk and above — all appointments, all patients (no billing)
require_front_desk_or_above = _require_roles("owner", "provider", "front_desk")
