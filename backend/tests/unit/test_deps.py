"""Unit tests for app/core/deps.py.

All async functions are called directly (no real DB or HTTP layer) using
AsyncMock sessions and controlled JWT payloads.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import (
    _get_token,
    _get_token_payload,
    get_current_tenant,
    get_current_user,
    require_front_desk_or_above,
    require_owner,
    require_provider_or_above,
)
from app.core.security import create_access_token
from app.models.user import User


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
) -> User:
    user = User()
    user.id = user_id or uuid.uuid4()
    user.tenant_id = tenant_id or uuid.uuid4()
    user.role = role
    user.is_active = is_active
    user.email = "test@example.com"
    user.full_name = "Test User"
    user.password_hash = "irrelevant"
    return user


def _mock_db(user: User | None = None) -> AsyncMock:
    db = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db.execute.return_value = result
    return db


# ── _get_token ────────────────────────────────────────────────────────────────

def test_get_token_valid_bearer() -> None:
    assert _get_token(authorization="Bearer abc.def.ghi") == "abc.def.ghi"


def test_get_token_missing_header_raises_401() -> None:
    with pytest.raises(HTTPException) as exc:
        _get_token(authorization=None)
    assert exc.value.status_code == 401


def test_get_token_no_bearer_prefix_raises_401() -> None:
    with pytest.raises(HTTPException) as exc:
        _get_token(authorization="Token abc.def.ghi")
    assert exc.value.status_code == 401


def test_get_token_bare_word_raises_401() -> None:
    with pytest.raises(HTTPException) as exc:
        _get_token(authorization="abc.def.ghi")
    assert exc.value.status_code == 401


# ── _get_token_payload ────────────────────────────────────────────────────────

async def test_get_token_payload_valid_access_token() -> None:
    token = create_access_token(uuid.uuid4(), uuid.uuid4(), "owner")
    payload = await _get_token_payload(token)
    assert payload["type"] == "access"
    assert "sub" in payload
    assert "tenant_id" in payload


async def test_get_token_payload_garbage_raises_401() -> None:
    with pytest.raises(HTTPException) as exc:
        await _get_token_payload("not.a.jwt")
    assert exc.value.status_code == 401


async def test_get_token_payload_refresh_token_raises_401() -> None:
    from app.core.security import create_refresh_token
    token = create_refresh_token(uuid.uuid4(), uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        await _get_token_payload(token)
    assert exc.value.status_code == 401


# ── get_current_tenant ────────────────────────────────────────────────────────

async def test_get_current_tenant_returns_uuid_and_sets_rls() -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    token = create_access_token(user_id, tenant_id, "owner")
    from app.core.deps import _get_token_payload
    payload = await _get_token_payload(token)

    db = _mock_db()
    result = await get_current_tenant(payload=payload, db=db)

    assert result == tenant_id
    # Verify SET LOCAL was executed with the correct tenant_id
    db.execute.assert_called_once()
    call_args = db.execute.call_args
    # The second positional/keyword arg is the bind params dict
    params = call_args[1].get("params") or (call_args[0][1] if len(call_args[0]) > 1 else call_args.kwargs.get("params"))
    # Check the executed statement contains our tenant_id string
    executed_sql = str(call_args[0][0])
    assert "app.current_tenant" in executed_sql


async def test_get_current_tenant_invalid_tenant_uuid_raises() -> None:
    payload = {"sub": str(uuid.uuid4()), "tenant_id": "not-a-uuid", "role": "owner", "type": "access"}
    db = _mock_db()
    with pytest.raises((ValueError, HTTPException)):
        await get_current_tenant(payload=payload, db=db)


# ── get_current_user ──────────────────────────────────────────────────────────

async def test_get_current_user_returns_active_user() -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    user = _make_user(user_id=user_id, tenant_id=tenant_id)

    payload = {"sub": str(user_id), "tenant_id": str(tenant_id), "role": "owner", "type": "access"}
    db = _mock_db(user=user)

    result = await get_current_user(payload=payload, tenant_id=tenant_id, db=db)
    assert result is user


async def test_get_current_user_inactive_raises_401() -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    user = _make_user(user_id=user_id, tenant_id=tenant_id, is_active=False)

    payload = {"sub": str(user_id), "tenant_id": str(tenant_id), "role": "owner", "type": "access"}
    db = _mock_db(user=user)

    with pytest.raises(HTTPException) as exc:
        await get_current_user(payload=payload, tenant_id=tenant_id, db=db)
    assert exc.value.status_code == 401


async def test_get_current_user_not_found_raises_401() -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()

    payload = {"sub": str(user_id), "tenant_id": str(tenant_id), "role": "owner", "type": "access"}
    db = _mock_db(user=None)

    with pytest.raises(HTTPException) as exc:
        await get_current_user(payload=payload, tenant_id=tenant_id, db=db)
    assert exc.value.status_code == 401


async def test_get_current_user_tenant_mismatch_raises_401() -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    # User's tenant_id in DB differs from JWT tenant claim
    user = _make_user(user_id=user_id, tenant_id=uuid.uuid4())

    payload = {"sub": str(user_id), "tenant_id": str(tenant_id), "role": "owner", "type": "access"}
    db = _mock_db(user=user)

    with pytest.raises(HTTPException) as exc:
        await get_current_user(payload=payload, tenant_id=tenant_id, db=db)
    assert exc.value.status_code == 401


# ── Role-checking dependencies ────────────────────────────────────────────────

async def _call_role_dep(dep_fn, user: User) -> User:
    """Call a role-checking dependency function with a pre-built user."""
    return await dep_fn(current_user=user)


async def test_require_owner_passes_for_owner() -> None:
    user = _make_user(role="owner")
    result = await _call_role_dep(require_owner, user)
    assert result is user


async def test_require_owner_rejects_provider() -> None:
    user = _make_user(role="provider")
    with pytest.raises(HTTPException) as exc:
        await _call_role_dep(require_owner, user)
    assert exc.value.status_code == 403


async def test_require_owner_rejects_front_desk() -> None:
    user = _make_user(role="front_desk")
    with pytest.raises(HTTPException) as exc:
        await _call_role_dep(require_owner, user)
    assert exc.value.status_code == 403


async def test_require_provider_or_above_passes_for_owner() -> None:
    user = _make_user(role="owner")
    result = await _call_role_dep(require_provider_or_above, user)
    assert result is user


async def test_require_provider_or_above_passes_for_provider() -> None:
    user = _make_user(role="provider")
    result = await _call_role_dep(require_provider_or_above, user)
    assert result is user


async def test_require_provider_or_above_rejects_front_desk() -> None:
    user = _make_user(role="front_desk")
    with pytest.raises(HTTPException) as exc:
        await _call_role_dep(require_provider_or_above, user)
    assert exc.value.status_code == 403


async def test_require_front_desk_or_above_passes_all_staff_roles() -> None:
    for role in ("owner", "provider", "front_desk"):
        user = _make_user(role=role)
        result = await _call_role_dep(require_front_desk_or_above, user)
        assert result is user


async def test_require_front_desk_or_above_rejects_unknown_role() -> None:
    user = _make_user(role="patient")
    with pytest.raises(HTTPException) as exc:
        await _call_role_dep(require_front_desk_or_above, user)
    assert exc.value.status_code == 403
