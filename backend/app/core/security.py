import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt as _bcrypt
from jose import JWTError, jwt

from app.core.config import get_settings

_ALGORITHM = "HS256"
_ACCESS_TYPE = "access"
_REFRESH_TYPE = "refresh"
_PORTAL_TYPE = "portal"


class TokenError(Exception):
    pass


# ── Passwords ─────────────────────────────────────────────────────────────────
# Using bcrypt directly — passlib 1.7.4 is incompatible with bcrypt >= 4.0.

def hash_password(plain: str) -> str:
    return _bcrypt.hashpw(plain.encode(), _bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return _bcrypt.checkpw(plain.encode(), hashed.encode())


# ── Refresh token storage hash ────────────────────────────────────────────────

def hash_refresh_token(raw_token: str) -> str:
    """SHA-256 hex digest stored in DB — never store the raw token."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


# ── JWT creation ──────────────────────────────────────────────────────────────

def create_access_token(
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    role: str,
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "role": role,
        "type": _ACCESS_TYPE,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=_ALGORITHM)


def create_refresh_token(
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "type": _REFRESH_TYPE,
        "jti": str(uuid.uuid4()),  # unique per token — enables per-token revocation
        "iat": now,
        "exp": now + timedelta(days=settings.refresh_token_expire_days),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=_ALGORITHM)


# ── JWT decoding ──────────────────────────────────────────────────────────────

def create_portal_token(
    patient_id: uuid.UUID,
    appointment_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub":            str(patient_id),
        "appointment_id": str(appointment_id),
        "tenant_id":      str(tenant_id),
        "type":           _PORTAL_TYPE,
        "iat":            now,
        "exp":            now + timedelta(minutes=30),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=_ALGORITHM)


def decode_token(token: str, expected_type: str) -> dict[str, Any]:
    """Decode and validate a JWT. Raises TokenError on any failure."""
    settings = get_settings()
    try:
        payload: dict[str, Any] = jwt.decode(
            token, settings.secret_key, algorithms=[_ALGORITHM]
        )
    except JWTError as exc:
        raise TokenError(f"Invalid token: {exc}") from exc

    if payload.get("type") != expected_type:
        raise TokenError(
            f"Expected token type '{expected_type}', got '{payload.get('type')}'"
        )
    return payload
