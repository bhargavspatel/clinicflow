# Phase 3 — Core Models Session Log
**Date:** 2026-05-10  
**Tests at close:** 107 passed, 0 failed (49 in `tests/api/`, 58 carry-over from Phases 1–2)

---

## What was built

Phase 3 delivered 7 new SQLAlchemy models, one Alembic migration covering all new tables plus retroactive RLS on Phase 2 tables, Pydantic schemas for patients/providers/appointments, three services with full business logic (including status-transition validation), three sets of CRUD endpoints, and a 40-test API integration suite with explicit tenant-isolation coverage.

---

## Files created

### Models

| File | Purpose |
|------|---------|
| `backend/app/models/patient.py` | `Patient`: tenant_id, full_name, phone (E.164), email?, dob?, notes?. Index on tenant_id. |
| `backend/app/models/provider.py` | `Provider`: tenant_id, user_id (FK→users), specialty, is_active (default True). Indexes on tenant_id and user_id. |
| `backend/app/models/appointment.py` | `Appointment`: all scheduling fields + risk_score/risk_bucket/last_scored_at for Phase 4 scoring. Composite indexes `(tenant_id, scheduled_at)` and `(tenant_id, status)`. |
| `backend/app/models/appointment_event.py` | `AppointmentEvent`: append-only audit log. No `TimestampMixin` (no `updated_at`). Python attr `event_metadata` maps to DB column `"metadata"` (avoid SQLAlchemy reserved name). `actor_id` is a plain UUID with no FK — null for system events. |
| `backend/app/models/notification.py` | `Notification`: channel (sms/email), direction (outbound/inbound), twilio_sid?, sent_at?, delivered_at?. Used in Phase 5. |
| `backend/app/models/waitlist.py` | `WaitlistEntry`: `preferred_days TEXT[]` (Postgres array), `provider_id` nullable with `ondelete="SET NULL"`, `filled_at?`. Used in Phase 6. |
| `backend/app/models/subscription.py` | `Subscription`: `UniqueConstraint("tenant_id")`, stripe_subscription_id UNIQUE, sms_usage_count/limit defaults 0/500. Used in Phase 7. |

### Migration

| File | Purpose |
|------|---------|
| `backend/alembic/versions/8eb0aa5f5f2b_add_patients_providers_appointments_.py` | Creates all 7 tables in dependency order. Adds all indexes. Applies RLS to 9 tables: `users` and `refresh_tokens` (retroactive) + all 7 new tables. |

### Schemas

| File | Purpose |
|------|---------|
| `backend/app/schemas/patient.py` | `PatientCreate` (E.164 phone validator), `PatientUpdate` (all Optional), `PatientOut` (from_attributes), `PatientListOut` (items + total + page + page_size). |
| `backend/app/schemas/provider.py` | `ProviderCreate`, `ProviderUpdate`, `ProviderOut`. |
| `backend/app/schemas/appointment.py` | `AppointmentCreate`, `AppointmentUpdate`, `AppointmentOut`, `AppointmentListOut`. `AppointmentType` and `AppointmentStatus` are `Literal` types — FastAPI validates at parse time, invalid values never reach the service. |

### Services

| File | Purpose |
|------|---------|
| `backend/app/services/patient_service.py` | `list_patients` (ILIKE search on name+phone, paginated), `get_patient`, `create_patient`, `update_patient`. Raises `NotFoundError`. |
| `backend/app/services/provider_service.py` | `list_providers` (optional `is_active` filter), `get_provider`, `create_provider` (duplicate-user guard), `update_provider`. Raises `NotFoundError`, `ConflictError`. |
| `backend/app/services/appointment_service.py` | `list_appointments` (5 independent filters + pagination), `get_appointment`, `create_appointment` (appends `"created"` event), `update_appointment` (validates transition, appends new-status event). Raises `NotFoundError`, `InvalidTransitionError`. |

### Endpoints

| File | Purpose |
|------|---------|
| `backend/app/api/v1/endpoints/patients.py` | `GET/POST /patients`, `GET/PATCH /patients/{id}`. |
| `backend/app/api/v1/endpoints/providers.py` | `GET/POST /providers`, `GET/PATCH /providers/{id}`. |
| `backend/app/api/v1/endpoints/appointments.py` | `GET/POST /appointments`, `GET/PATCH /appointments/{id}`. |

### Tests

| File | Count | What it covers |
|------|-------|---------------|
| `backend/tests/api/helpers.py` | — | `register_clinic`, `auth()`, `get_me`, `create_patient`, `create_provider`, `create_appointment` — shared helpers for all API tests. |
| `backend/tests/api/test_patients.py` | 10 | Create, get, 404, partial PATCH, list+total, search by name, search by phone, page_size, invalid E.164 → 422, unauthenticated → 401. |
| `backend/tests/api/test_providers.py` | 8 | Create, duplicate user → 409, list, get, 404, PATCH specialty, `is_active` filter, unauthenticated → 401. |
| `backend/tests/api/test_appointments.py` | 13 | Create+defaults, invalid type → 422, get, 404, `scheduled→confirmed`, `scheduled→confirmed→completed`, `scheduled→completed` (skip) → 422, terminal `cancelled` blocks all further transitions, `no_show` is terminal, filter by status, filter by date range, filter by provider_id, pagination, unauthenticated → 401. |
| `backend/tests/api/test_tenant_isolation.py` | 9 | Patient GET/list/PATCH blocked across tenants; provider GET/list scoped; appointment GET/list blocked; cross-tenant PATCH returns 404 and leaves row unchanged. |

---

## Files modified

| File | Change |
|------|--------|
| `backend/app/models/__init__.py` | Added 7 new model imports; all 10 models now exported. |
| `backend/app/api/v1/__init__.py` | Registered `patients`, `providers`, and `appointments` routers. |

---

## Endpoints registered

```
GET    /api/v1/patients                  → 200 PatientListOut       (search, page, page_size)
POST   /api/v1/patients                  → 201 PatientOut
GET    /api/v1/patients/{id}             → 200 PatientOut
PATCH  /api/v1/patients/{id}             → 200 PatientOut

GET    /api/v1/providers                 → 200 list[ProviderOut]     (is_active filter)
POST   /api/v1/providers                 → 201 ProviderOut
GET    /api/v1/providers/{id}            → 200 ProviderOut
PATCH  /api/v1/providers/{id}            → 200 ProviderOut

GET    /api/v1/appointments              → 200 AppointmentListOut    (date_from, date_to, status, provider_id, risk_bucket, page, page_size)
POST   /api/v1/appointments              → 201 AppointmentOut
GET    /api/v1/appointments/{id}         → 200 AppointmentOut
PATCH  /api/v1/appointments/{id}         → 200 AppointmentOut
```

All endpoints require a valid Bearer token. `get_current_tenant` fires first on every request — sets `app.current_tenant` in Postgres session config, activating RLS before any query runs. Services also filter by `tenant_id` explicitly (belt-and-suspenders).

---

## RLS migration details

The migration (`8eb0aa5f5f2b`) applies RLS to 9 tables total:

```python
_RLS_TABLES = [
    "users", "refresh_tokens",          # retroactive — were missing policies
    "patients", "providers", "appointments",
    "appointment_events", "notifications", "waitlist", "subscriptions",
]

for table in _RLS_TABLES:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)"
    )
```

- `FORCE ROW LEVEL SECURITY` makes the policy apply to the table owner (app DB role). Superusers (`BYPASSRLS`) are unaffected — test fixtures use the `clinicflow` superuser to bypass RLS for seeding.
- `NULLIF(..., '')` handles the case where `app.current_tenant` is unset: `current_setting` returns `''` (not NULL) when `missing_ok=true`. Casting `''` to UUID would raise an error; `NULLIF` converts it to NULL first, so an unscoped session sees zero rows rather than an error.
- `tenants` table deliberately has no RLS — subdomain lookup during login must work before a JWT exists.

---

## Status transition rules

Encoded in `appointment_service._VALID_TRANSITIONS`:

```
scheduled   → confirmed, rescheduled, cancelled, no_show
confirmed   → rescheduled, completed, no_show, cancelled
rescheduled → confirmed, cancelled, no_show
completed   → (terminal — no outbound transitions)
no_show     → (terminal)
cancelled   → (terminal)
```

Any transition not in the above map raises `InvalidTransitionError` → HTTP 422.  
Every valid status change appends a row to `appointment_events` with `event_type = <new_status>` and `actor_id = current_user.id`.  
Appointment creation always appends an `event_type = "created"` event.

---

## Key decisions

### `appointment_events` has no `updated_at`
The table is append-only — adding `TimestampMixin` would give every row an `updated_at` column that could never have a meaningful value. Model comment: `APPEND-ONLY — never call session.delete() or issue UPDATE queries on this model.`

### `event_metadata` Python attr → `"metadata"` DB column
`metadata` is a reserved name on SQLAlchemy's `DeclarativeBase`. Using it as a column name produces a silent attribute-shadowing bug. The Python attr is named `event_metadata` with `mapped_column("metadata", JSONB, ...)` to preserve the DB column name from the schema doc.

### `actor_id` on events has no FK
System-generated events (e.g. nightly risk scoring) have no actor. A FK to `users` would require either a nullable FK (allowed) or a synthetic "system" user row. Chose a plain nullable UUID with no FK. This means `actor_id` values aren't validated at the DB level — the application layer is responsible for providing valid user IDs.

### `db.flush()` before event creation
`create_appointment` calls `await db.flush()` after `db.add(appt)` so that `appt.id` is populated (from the `default=uuid.uuid4` on the model) before constructing the `AppointmentEvent`. Without the flush, `appt.id` would be a Python-generated UUID that hasn't been confirmed by the DB yet — still correct since we generate UUIDs in Python, but the flush makes the sequence explicit and safe.

### `Literal` types for `appointment_type` and `status` in schemas
Pydantic validates enum membership before the request reaches the service. An invalid `appointment_type` returns 422 with a clear error at the schema layer, without the service needing its own validation. The service's `_VALID_TRANSITIONS` dict then handles legal-but-invalid *transitions* (valid status values in an invalid sequence).

### `actor_id` threaded from endpoint to service
`create_appointment` and `update_appointment` in the service accept `actor_id: Optional[uuid.UUID] = None`. The endpoint passes `current_user.id`. This keeps the service free of FastAPI imports while still recording who made each change in the audit trail.

### No `DELETE` endpoints
No `DELETE /patients/{id}`, `DELETE /providers/{id}`, or `DELETE /appointments/{id}` were built. Intentional: patient records and appointment history should not be hard-deleted (HIPAA audit trail requirements). Soft-delete (an `is_active` flag or `deleted_at` timestamp) will be addressed if/when needed.

---

## Test coverage by layer

| File | Tests | Notes |
|------|-------|-------|
| `tests/api/test_patients.py` | 10 | Full CRUD, search, pagination, validation |
| `tests/api/test_providers.py` | 8 | Full CRUD, active filter, duplicate guard |
| `tests/api/test_appointments.py` | 13 | Full CRUD, 5 transition tests, 3 filter tests, pagination |
| `tests/api/test_tenant_isolation.py` | 9 | Cross-tenant GET/list/PATCH blocked for all 3 entities |
| **Phase 3 total** | **40** | |
| Carry-over from Phases 1–2 | 58 | `test_security`, `test_deps`, `test_auth_service`, `test_auth` |
| **Session total** | **107** | All passing, 0 failed |

---

## Open questions

1. **No cross-FK tenant validation on appointment create.** `POST /appointments` accepts any `patient_id` and `provider_id` UUIDs. The service sets `appointment.tenant_id` from the JWT, but does not verify that the referenced patient and provider belong to the same tenant. RLS means the rows would be invisible to the attacker's own queries, but the FK rows themselves exist in the DB across tenant boundaries. A guard query (`SELECT 1 FROM patients WHERE id=? AND tenant_id=?`) should be added to `create_appointment` before Phase 3 goes to production.

2. **No soft-delete.** Hard deletes cascade correctly via FK constraints, but there is no `deleted_at` / `is_active` flag on `patients`, `appointments`, or `providers`. HIPAA may require retaining patient records. Decision needed before any delete endpoint is added.

3. **`waitlist` and `notification` models have no CRUD endpoints.** The models and migration exist (ready for Phase 5/6) but no routes are registered. `notification` is write-only from the worker side; `waitlist` needs `POST /waitlist`, `DELETE /waitlist/{id}`, and query by patient.

4. **No `POST /users` (staff invite) endpoint.** The owner can't yet add provider or front-desk staff accounts. The auth layer is complete but the invite flow (owner creates user → user receives magic link → user sets password) is not built. Needed before any real clinic can use the system.

5. **`risk_score`, `risk_bucket`, `last_scored_at` are nullable placeholders.** These columns exist on `appointments` and are ready for Phase 4 (risk scoring service + nightly Arq batch). The list endpoint already accepts `risk_bucket` as a filter — it will return no results until Phase 4 runs.

6. **No `GET /appointments/{id}/events` endpoint.** `appointment_events` rows are created correctly (verified by service logic) but there is no API to query them. Needed for the frontend audit trail view in Phase 8.

7. **`appointment_events.actor_id` has no FK.** A malformed actor_id would silently persist without a DB-level constraint. Low-risk for now (only internal code writes events), but worth adding a FK to `users(id)` with `ON DELETE SET NULL` in a future migration.

---

## Next session — Phase 4 starting point

1. Read `docs/05-risk-scoring-rubric.md` before touching any code.
2. Build `app/services/risk_scoring_service.py` — pure function `score_appointment(appointment, history) → (int, str)`, no DB access, fully unit-testable.
3. Build `app/workers/scoring_worker.py` — Arq task that fetches unscored appointments, calls the scoring service, writes `risk_score`/`risk_bucket`/`last_scored_at`, and appends a `"scored"` event.
4. Register the worker in the Arq settings and add it to `docker-compose.yml`.
5. Write unit tests for the scoring algorithm (all rubric edge cases) and an integration test that enqueues the task and asserts the appointment row is updated.
6. Add `POST /users` (staff invite) to unblock real multi-user clinic setups.
