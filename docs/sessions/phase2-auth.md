# Phase 2 — Auth Layer Session Log
**Date:** 2026-05-10  
**Tests at close:** 67 passed, 0 failed

---

## What was built

Phase 2 delivered the complete authentication layer: JWT tokens, password hashing, FastAPI dependencies, Pydantic schemas, the auth service, HTTP endpoints, and a full integration test suite against a real PostgreSQL test database.

---

## Files created

| File | Purpose |
|------|---------|
| `backend/app/core/security.py` | JWT creation/decoding (HS256), bcrypt password hashing, SHA-256 refresh token storage hash. Zero FastAPI imports. |
| `backend/app/core/deps.py` | FastAPI dependencies: `get_current_tenant` (sets Postgres RLS context), `get_current_user` (validates JWT + fetches user row), `require_owner` / `require_provider_or_above` / `require_front_desk_or_above` (role-checking callables). |
| `backend/app/schemas/auth.py` | Pydantic v2 request/response models: `RegisterRequest`, `LoginRequest`, `TokenResponse`, `RefreshRequest`, `UserOut`. |
| `backend/app/services/auth_service.py` | Business logic: `register`, `login`, `refresh`, `logout`. Zero FastAPI imports; raises `AuthError` / `ConflictError`. |
| `backend/app/api/v1/endpoints/auth.py` | Four POST routes + `GET /me`. Thin handlers — maps service exceptions to HTTPException. |
| `backend/tests/unit/test_security.py` | 18 unit tests for `security.py`. |
| `backend/tests/unit/test_deps.py` | 21 unit tests for `deps.py` (mocked AsyncSession). |
| `backend/tests/services/test_auth_service.py` | 19 unit tests for `auth_service.py` (mocked AsyncSession). |
| `backend/tests/api/test_auth.py` | 9 integration tests against real `clinicflow_test` DB via httpx AsyncClient. |

## Files modified

| File | Change |
|------|--------|
| `backend/app/core/security.py` | Added `jti: uuid4()` claim to refresh tokens (makes every token unique even within the same second). |
| `backend/app/schemas/auth.py` | Added `subdomain` field to `LoginRequest` (required for multi-tenant login routing). |
| `backend/app/api/v1/__init__.py` | Uncommented and wired `auth.router` at prefix `/auth`. |
| `backend/app/main.py` | Uncommented `v1_router` mount at `/api/v1`. |
| `backend/tests/conftest.py` | Added session-scoped `_setup_test_db` (sync, `asyncio.run`), per-test `client` (httpx + `get_db` override + table truncation), and `seed_db` (superuser direct session for assertions) fixtures. |

---

## Endpoints registered

```
POST   /api/v1/auth/register   → 201 TokenResponse  (creates tenant + owner)
POST   /api/v1/auth/login      → 200 TokenResponse
POST   /api/v1/auth/refresh    → 200 TokenResponse  (rotates token)
POST   /api/v1/auth/logout     → 204 No Content     (revokes token)
GET    /api/v1/auth/me         → 200 UserOut        (requires valid access token)
```

---

## Key decisions

### `bcrypt` directly, not `passlib`
`passlib 1.7.4` is incompatible with `bcrypt >= 4.0` (wrap-bug detection breaks). Dropped passlib entirely; call `_bcrypt.hashpw` / `_bcrypt.checkpw` directly since bcrypt is already a transitive dependency.

### `set_config()` instead of `SET LOCAL` for RLS context
`SET LOCAL app.current_tenant = $1` is invalid PostgreSQL syntax — `SET` is a utility command, not DML, and does not accept bind parameters. The integration tests caught this immediately.  
Fix: `SELECT set_config('app.current_tenant', :tid, true)` — a normal SQL function call, fully parameterized. The third argument `true` makes it transaction-scoped (identical semantics to `SET LOCAL`).

### `jti` in refresh tokens
Without a `jti` (JWT ID) claim, two refresh tokens created within the same second for the same user are byte-identical (same `iat`/`exp`). This broke `test_refresh_rotates_token_and_revokes_old`. Added `"jti": str(uuid.uuid4())` to every refresh token. Side benefit: enables per-token revocation by JTI in the future.

### Email unique per tenant, not globally
`UniqueConstraint("tenant_id", "email")` — the same staff email can exist in two independent clinics. The integration test `test_two_tenants_see_only_their_own_user` exercises this with `alice@sunrise.com` in both tenants to confirm isolation.

### Subdomain on `LoginRequest`
The multi-tenant login endpoint needs to resolve `subdomain → tenant_id` before validating credentials. The route does one `SELECT` on the `tenants` table (no RLS on that table), then delegates to `auth_service.login(db, email, password, tenant_id)`. Both a missing and an inactive subdomain return the same 401 as a bad password to avoid leaking subdomain existence.

### Constant-time login path
`auth_service.login` always calls `verify_password` regardless of whether the user exists, using `_DUMMY_HASH` for the no-user branch. Prevents email-enumeration via response-time oracle. Dummy hash is computed once at module import with `bcrypt.gensalt(rounds=4)` (< 1 ms).

### `services` layer has zero FastAPI imports
`auth_service.py` raises `AuthError` and `ConflictError`; the route handler maps these to `HTTPException`. This is enforced by an import-time assertion in the test suite that no `fastapi` symbol leaks into the service module.

### `dep_overrides` approach for integration tests
The `client` fixture in `conftest.py` overrides `get_db` so every HTTP request during the test uses `clinicflow_test`, not `clinicflow`. Tables are truncated before each test (via `TRUNCATE tenants CASCADE`). The `seed_db` fixture provides a direct superuser session for asserting DB state after API calls — the `clinicflow` user is `POSTGRES_USER` and therefore bypasses RLS automatically.

### Session-scoped DB setup is sync
`pytest-asyncio 0.23.x` uses per-function event loops by default. Session-scoped async fixtures would require overriding `event_loop` scope (deprecated). Instead, `_setup_test_db` is a plain sync fixture that calls `asyncio.run()`, which creates and tears down its own isolated event loop — no conflict with pytest-asyncio's loops.

---

## Test coverage by layer

| Layer | File | Count |
|-------|------|-------|
| Unit — security primitives | `test_security.py` | 18 |
| Unit — FastAPI dependencies | `test_deps.py` | 21 |
| Unit — auth service (mocked DB) | `test_auth_service.py` | 19 |
| Integration — HTTP + real DB | `test_auth.py` | 9 |
| **Total** | | **67** |

---

## Open questions

1. **RLS policies not yet in the database.** `get_current_tenant` and `auth_service._set_rls` call `set_config` to set `app.current_tenant`, but no `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` + `CREATE POLICY` migration exists yet. The integration tests pass because there are no RLS policies to enforce. A follow-up migration must add policies to every tenant-scoped table before Phase 3 data is live.

2. **Tenant registration flow is self-serve.** `POST /auth/register` creates a new tenant + owner in one call with no invite gate. If you want clinic onboarding to require manual approval or a Stripe checkout step first, the register endpoint needs to be gated.

3. **Staff invitation flow not defined.** `POST /users` (invite a new provider or front-desk user) is in the API contracts but not built yet. The owner creates staff accounts; staff don't self-register. This is Phase 3+ work.

4. **Magic-link auth for patients is not started.** `POST /auth/magic-link/send` and `GET /auth/magic-link/verify` are listed in the API contracts but belong to the patient portal work in Phase 6.

5. **Email provider for magic links is unselected.** Candidates: Resend, SendGrid, AWS SES. Needs a decision before Phase 6.

6. **`GET /auth/me` was added opportunistically** (needed for the "protected endpoint" integration test). It is not in the original API contracts doc — either add it there or remove the endpoint and find another protected route for the test once Phase 3 models exist.

---

## Next session — Phase 3 starting point

1. Add RLS migration: `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` + `CREATE POLICY` for `users`, `refresh_tokens`, and all future tables.
2. Build `patients` model + Alembic migration.
3. Build `providers` model + Alembic migration.
4. Build `appointments` model + Alembic migration.
5. `POST/GET /patients`, `POST/GET /providers`, `POST/GET /appointments` CRUD endpoints.
6. Add `tenant_factory` and `user_factory` fixtures to `conftest.py` to make future integration tests less verbose.
