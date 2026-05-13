"""Unit tests for app/services/auth_service.py.

All tests use AsyncMock sessions — no real database required.
The goal is to assert actual output/side-effects, not just that mocks were called.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import get_settings
from app.core.security import create_refresh_token, hash_refresh_token
from app.models.refresh_token import RefreshToken
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.auth import RegisterRequest, TokenResponse
from app.services.auth_service import (
    AuthError,
    ConflictError,
    login,
    logout,
    refresh,
    register,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _test_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-32-chars-minimum!")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/test")
    get_settings.cache_clear()


def _make_user(
    role: str = "owner",
    is_active: bool = True,
    user_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    password_hash: str = "",
) -> User:
    from app.core.security import hash_password
    u = User()
    u.id = user_id or uuid.uuid4()
    u.tenant_id = tenant_id or uuid.uuid4()
    u.email = "owner@clinic.com"
    u.full_name = "Test Owner"
    u.role = role
    u.is_active = is_active
    u.password_hash = password_hash or hash_password("correct-password")
    return u


def _make_stored_token(
    user: User,
    revoked: bool = False,
    expired: bool = False,
    raw_token: str | None = None,
) -> RefreshToken:
    now = datetime.now(timezone.utc)
    rt = RefreshToken()
    rt.id = uuid.uuid4()
    rt.user_id = user.id
    rt.tenant_id = user.tenant_id
    rt.token_hash = hash_refresh_token(raw_token or "raw-token")
    rt.expires_at = now - timedelta(seconds=1) if expired else now + timedelta(days=7)
    rt.revoked_at = now if revoked else None
    return rt


def _mock_db(
    first_query_result=None,
    second_query_result=None,
) -> AsyncMock:
    """Build an AsyncSession mock that returns different results per execute call."""
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.flush = AsyncMock()

    results = [first_query_result, second_query_result]
    call_count = {"n": 0}

    async def _execute(stmt, *args, **kwargs):
        # SET LOCAL calls are fire-and-forget; return a no-op mock for them
        sql = str(stmt)
        if "SET LOCAL" in sql or "set_config" in sql:
            return MagicMock()
        result = MagicMock()
        idx = min(call_count["n"], len(results) - 1)
        result.scalar_one_or_none.return_value = results[idx]
        call_count["n"] += 1
        return result

    db.execute = _execute
    return db


# ── register ──────────────────────────────────────────────────────────────────

async def test_register_conflict_on_existing_subdomain() -> None:
    existing_tenant = Tenant()
    existing_tenant.id = uuid.uuid4()

    db = _mock_db(first_query_result=existing_tenant)

    data = RegisterRequest(
        clinic_name="Sunrise PT",
        subdomain="sunrise-pt",
        owner_full_name="Alice Smith",
        owner_email="alice@sunrise.com",
        password="securepass1",
    )
    with pytest.raises(ConflictError, match="already taken"):
        await register(db, data)


async def test_register_creates_tenant_and_user_and_returns_tokens() -> None:
    # subdomain lookup returns None → proceed
    db = _mock_db(first_query_result=None)

    data = RegisterRequest(
        clinic_name="New Clinic",
        subdomain="new-clinic",
        owner_full_name="Bob Jones",
        owner_email="bob@newclinic.com",
        password="securepass1",
    )

    result = await register(db, data)

    assert isinstance(result, TokenResponse)
    assert result.token_type == "bearer"
    assert result.expires_in == get_settings().access_token_expire_minutes * 60
    assert len(result.access_token) > 0
    assert len(result.refresh_token) > 0

    # Tenant and User (and RefreshToken) must have been added to the session
    assert db.add.call_count == 3
    db.commit.assert_called_once()


async def test_register_rollback_on_integrity_error() -> None:
    from sqlalchemy.exc import IntegrityError

    db = _mock_db(first_query_result=None)
    db.commit = AsyncMock(side_effect=IntegrityError("dup", {}, Exception()))

    data = RegisterRequest(
        clinic_name="Dup Clinic",
        subdomain="dup-clinic",
        owner_full_name="Carol Lee",
        owner_email="carol@dup.com",
        password="securepass1",
    )
    with pytest.raises(ConflictError):
        await register(db, data)

    db.rollback.assert_called_once()


# ── login ─────────────────────────────────────────────────────────────────────

async def test_login_valid_credentials_returns_tokens() -> None:
    user = _make_user()
    db = _mock_db(first_query_result=user)

    result = await login(db, user.email, "correct-password", user.tenant_id)

    assert isinstance(result, TokenResponse)
    assert result.token_type == "bearer"
    db.commit.assert_called_once()


async def test_login_wrong_password_raises_auth_error() -> None:
    user = _make_user()
    db = _mock_db(first_query_result=user)

    with pytest.raises(AuthError, match="Invalid credentials"):
        await login(db, user.email, "wrong-password", user.tenant_id)


async def test_login_unknown_email_raises_auth_error() -> None:
    db = _mock_db(first_query_result=None)

    with pytest.raises(AuthError, match="Invalid credentials"):
        await login(db, "nobody@x.com", "any-pass", uuid.uuid4())


async def test_login_inactive_user_raises_auth_error() -> None:
    user = _make_user(is_active=False)
    db = _mock_db(first_query_result=user)

    with pytest.raises(AuthError, match="Invalid credentials"):
        await login(db, user.email, "correct-password", user.tenant_id)


async def test_login_does_not_commit_on_failure() -> None:
    db = _mock_db(first_query_result=None)

    with pytest.raises(AuthError):
        await login(db, "nobody@x.com", "pass", uuid.uuid4())

    db.commit.assert_not_called()


# ── refresh ───────────────────────────────────────────────────────────────────

async def test_refresh_valid_token_returns_new_pair() -> None:
    user = _make_user()
    raw = create_refresh_token(user.id, user.tenant_id)
    stored = _make_stored_token(user, raw_token=raw)

    db = _mock_db(first_query_result=stored, second_query_result=user)

    result = await refresh(db, raw)

    assert isinstance(result, TokenResponse)
    assert result.refresh_token != raw  # token was rotated
    # Old stored token must be revoked
    assert stored.revoked_at is not None
    db.commit.assert_called_once()


async def test_refresh_already_revoked_raises() -> None:
    user = _make_user()
    raw = create_refresh_token(user.id, user.tenant_id)
    stored = _make_stored_token(user, raw_token=raw, revoked=True)

    db = _mock_db(first_query_result=stored, second_query_result=user)

    with pytest.raises(AuthError, match="revoked"):
        await refresh(db, raw)


async def test_refresh_expired_stored_token_raises() -> None:
    user = _make_user()
    raw = create_refresh_token(user.id, user.tenant_id)
    stored = _make_stored_token(user, raw_token=raw, expired=True)

    db = _mock_db(first_query_result=stored, second_query_result=user)

    with pytest.raises(AuthError):
        await refresh(db, raw)


async def test_refresh_token_not_found_raises() -> None:
    user = _make_user()
    raw = create_refresh_token(user.id, user.tenant_id)

    db = _mock_db(first_query_result=None)

    with pytest.raises(AuthError):
        await refresh(db, raw)


async def test_refresh_garbage_jwt_raises() -> None:
    db = _mock_db()

    with pytest.raises(AuthError):
        await refresh(db, "not.a.jwt")


async def test_refresh_access_token_rejected() -> None:
    """Passing an access token to refresh should raise AuthError (wrong type)."""
    from app.core.security import create_access_token
    user = _make_user()
    access = create_access_token(user.id, user.tenant_id, user.role)
    db = _mock_db()

    with pytest.raises(AuthError):
        await refresh(db, access)


async def test_refresh_inactive_user_raises() -> None:
    user = _make_user(is_active=False)
    raw = create_refresh_token(user.id, user.tenant_id)
    stored = _make_stored_token(user, raw_token=raw)

    db = _mock_db(first_query_result=stored, second_query_result=user)

    with pytest.raises(AuthError, match="inactive"):
        await refresh(db, raw)


# ── logout ────────────────────────────────────────────────────────────────────

async def test_logout_valid_token_sets_revoked_at() -> None:
    user = _make_user()
    raw = create_refresh_token(user.id, user.tenant_id)
    stored = _make_stored_token(user, raw_token=raw)

    db = _mock_db(first_query_result=stored)

    await logout(db, raw)

    assert stored.revoked_at is not None
    db.commit.assert_called_once()


async def test_logout_already_revoked_raises() -> None:
    user = _make_user()
    raw = create_refresh_token(user.id, user.tenant_id)
    stored = _make_stored_token(user, raw_token=raw, revoked=True)

    db = _mock_db(first_query_result=stored)

    with pytest.raises(AuthError, match="already revoked"):
        await logout(db, raw)


async def test_logout_token_not_found_raises() -> None:
    user = _make_user()
    raw = create_refresh_token(user.id, user.tenant_id)

    db = _mock_db(first_query_result=None)

    with pytest.raises(AuthError, match="not found"):
        await logout(db, raw)


async def test_logout_garbage_jwt_raises() -> None:
    db = _mock_db()

    with pytest.raises(AuthError):
        await logout(db, "garbage.token.value")
