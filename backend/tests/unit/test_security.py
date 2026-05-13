import uuid
from datetime import datetime, timezone

import pytest

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


@pytest.fixture(autouse=True)
def _test_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a deterministic SECRET_KEY for every test and bust the lru_cache."""
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-32-chars-minimum!")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/test")
    get_settings.cache_clear()


# ── Password hashing ──────────────────────────────────────────────────────────

def test_hash_password_is_not_plaintext() -> None:
    assert hash_password("mysecret") != "mysecret"


def test_verify_password_correct() -> None:
    hashed = hash_password("correct-horse")
    assert verify_password("correct-horse", hashed) is True


def test_verify_password_wrong_returns_false() -> None:
    hashed = hash_password("correct-horse")
    assert verify_password("wrong-horse", hashed) is False


def test_hash_password_uses_unique_salts() -> None:
    # bcrypt salts ensure identical plaintexts produce different hashes
    assert hash_password("same") != hash_password("same")


# ── SHA-256 refresh token hash ────────────────────────────────────────────────

def test_hash_refresh_token_is_64_char_hex() -> None:
    result = hash_refresh_token("any-raw-token")
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)


def test_hash_refresh_token_is_deterministic() -> None:
    raw = "some-raw-token-value"
    assert hash_refresh_token(raw) == hash_refresh_token(raw)


def test_hash_refresh_token_different_inputs_differ() -> None:
    assert hash_refresh_token("token-a") != hash_refresh_token("token-b")


# ── Access token ──────────────────────────────────────────────────────────────

def test_create_access_token_returns_string() -> None:
    token = create_access_token(uuid.uuid4(), uuid.uuid4(), "owner")
    assert isinstance(token, str) and len(token) > 0


def test_decode_access_token_contains_correct_claims() -> None:
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    token = create_access_token(user_id, tenant_id, "provider")
    payload = decode_token(token, "access")

    assert payload["sub"] == str(user_id)
    assert payload["tenant_id"] == str(tenant_id)
    assert payload["role"] == "provider"
    assert payload["type"] == "access"


def test_access_token_expiry_is_15_minutes() -> None:
    token = create_access_token(uuid.uuid4(), uuid.uuid4(), "front_desk")
    payload = decode_token(token, "access")

    now = datetime.now(timezone.utc).timestamp()
    assert payload["exp"] > now
    # Allow 5 s clock skew in CI
    assert payload["exp"] <= now + 15 * 60 + 5


# ── Refresh token ─────────────────────────────────────────────────────────────

def test_create_refresh_token_returns_string() -> None:
    token = create_refresh_token(uuid.uuid4(), uuid.uuid4())
    assert isinstance(token, str) and len(token) > 0


def test_decode_refresh_token_contains_correct_claims() -> None:
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    token = create_refresh_token(user_id, tenant_id)
    payload = decode_token(token, "refresh")

    assert payload["sub"] == str(user_id)
    assert payload["tenant_id"] == str(tenant_id)
    assert payload["type"] == "refresh"
    assert "role" not in payload
    assert "jti" in payload  # unique per-token ID for revocation


def test_refresh_token_expiry_is_7_days() -> None:
    token = create_refresh_token(uuid.uuid4(), uuid.uuid4())
    payload = decode_token(token, "refresh")

    now = datetime.now(timezone.utc).timestamp()
    assert payload["exp"] > now + 6 * 24 * 60 * 60   # at least 6 days away
    assert payload["exp"] <= now + 7 * 24 * 60 * 60 + 5  # at most 7 days + skew


# ── decode_token error cases ──────────────────────────────────────────────────

def test_decode_token_wrong_type_raises() -> None:
    token = create_access_token(uuid.uuid4(), uuid.uuid4(), "owner")
    with pytest.raises(TokenError, match="Expected token type"):
        decode_token(token, "refresh")


def test_decode_token_refresh_as_access_raises() -> None:
    token = create_refresh_token(uuid.uuid4(), uuid.uuid4())
    with pytest.raises(TokenError, match="Expected token type"):
        decode_token(token, "access")


def test_decode_token_tampered_signature_raises() -> None:
    token = create_access_token(uuid.uuid4(), uuid.uuid4(), "owner")
    tampered = token[:-6] + "xxxxxx"
    with pytest.raises(TokenError):
        decode_token(tampered, "access")


def test_decode_token_garbage_string_raises() -> None:
    with pytest.raises(TokenError):
        decode_token("not.a.jwt.at.all", "access")


def test_decode_token_wrong_secret_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    token = create_access_token(uuid.uuid4(), uuid.uuid4(), "owner")

    monkeypatch.setenv("SECRET_KEY", "completely-different-secret-key!")
    get_settings.cache_clear()

    with pytest.raises(TokenError):
        decode_token(token, "access")
