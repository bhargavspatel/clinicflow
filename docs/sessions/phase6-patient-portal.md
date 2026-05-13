# Phase 6 — Patient Portal

**Date:** 2026-05-10
**Status:** Complete
**Tests:** 185 total (172 carry-over + 13 new)

---

## What was built

Phase 6 adds the patient-facing portal: magic link auth, appointment rescheduling,
and waitlist management. Patients never hold staff JWTs; instead they receive a
short-lived SMS link that exchanges into a portal JWT scoped to exactly one
appointment. A new Arq task auto-fills cancelled slots from the waitlist.

---

## Files

| File | Status | Description |
|------|--------|-------------|
| `app/core/security.py` | MODIFIED | Added `_PORTAL_TYPE = "portal"`, `create_portal_token(patient_id, appointment_id, tenant_id)` — 30-min HS256 JWT |
| `app/core/arq_pool.py` | NEW | Lazy `ArqRedis` singleton for the API process; used as `Depends(get_arq_pool)` |
| `app/core/deps.py` | MODIFIED | Added `get_portal_payload` — decodes `type="portal"` JWT, raises 401 on failure |
| `app/services/magic_link_service.py` | NEW | `create_magic_link_url`, `send_magic_link`, `verify_magic_link`, `generate_patient_jwt` |
| `app/services/reschedule_service.py` | NEW | `get_available_slots`, `reschedule_appointment` |
| `app/services/waitlist_service.py` | NEW | `add_to_waitlist`, `find_waitlist_match`, `notify_waitlist_patient` |
| `app/schemas/auth.py` | MODIFIED | Added `MagicLinkSendRequest`, `PortalTokenResponse` |
| `app/schemas/portal.py` | NEW | `SlotsResponse`, `RescheduleRequest`, `RescheduleOut` |
| `app/api/v1/endpoints/auth.py` | MODIFIED | `POST /auth/magic-link/send`, `GET /auth/magic-link/verify` |
| `app/api/v1/endpoints/portal.py` | NEW | `GET /portal/slots`, `POST /portal/reschedule` |
| `app/api/v1/__init__.py` | MODIFIED | Registered `portal.router` at `/portal` |
| `app/api/v1/endpoints/appointments.py` | MODIFIED | PATCH injects `get_arq_pool` as `Depends`; enqueues `fill_waitlist_task` on cancellation |
| `app/workers/waitlist_worker.py` | NEW | `fill_waitlist_task` — idempotent Arq task enqueued on cancellation |
| `app/workers/main.py` | MODIFIED | Registered `fill_waitlist_task` in `functions` list |
| `app/workers/notification_worker.py` | MODIFIED | Replaced placeholder magic link with real `create_magic_link_url` call |
| `tests/api/test_magic_link.py` | NEW | 3 tests: generate, verify, expired |
| `tests/api/test_portal.py` | NEW | 5 tests: slots, reschedule success/rejected/occupied, portal JWT required |
| `tests/unit/test_waitlist.py` | NEW | 5 tests: add entry, 2h window guard, time-pref filter, full integration, idempotency |
| `tests/conftest.py` | MODIFIED | `client` fixture now overrides `get_arq_pool` with `AsyncMock` so no test needs Redis |

---

## Magic link auth — `magic_link_service.py`

### Token lifecycle

```
staff POST /auth/magic-link/send
  → send_magic_link(db, appointment_id, tenant_id, redis, twilio_client, from_number)
      1. Verify appointment exists and belongs to tenant
      2. Reject if appointment status ∉ {scheduled, rescheduled, confirmed}
      3. secrets.token_urlsafe(32) → random token
      4. redis.set("magic_link:{token}", JSON{appointment_id, patient_id, tenant_id}, ex=1800)
      5. Build URL: https://{subdomain}.clinicflow.app/reschedule?token={token}
      6. SMS patient via Twilio (sync executor)
      7. Return raw token

patient GET /auth/magic-link/verify?token=...
  → verify_magic_link(token, redis)
      1. redis.get("magic_link:{token}") — None → MagicLinkError (→ 401)
      2. redis.delete(key)   ← single-use enforcement
      3. return json.loads(raw)
  → generate_patient_jwt(patient_id, appointment_id, tenant_id)
      → create_portal_token → HS256 JWT, type="portal", exp=30min
  → PortalTokenResponse{access_token, token_type, expires_in=1800, patient_id, appointment_id}
```

### `create_magic_link_url` — split from `send_magic_link`

The notification worker also needs to embed a real reschedule URL in SMS reminders
without triggering a second SMS send. `create_magic_link_url` handles only token
generation + Redis caching; `send_magic_link` calls it internally and then dispatches
the SMS. `notification_worker.send_sms_task` calls `create_magic_link_url` directly.

### Token security properties

- **Single-use:** `verify_magic_link` deletes the Redis entry before returning, so
  clicking the link twice fails on the second attempt.
- **30-minute TTL:** enforced by Redis `ex=1800`; the portal JWT also expires in 30 min.
- **Tenant-scoped:** `send_magic_link` checks `appt.tenant_id == tenant_id` before
  generating a token, preventing a staff member from one tenant generating a link for
  another tenant's appointment.
- **Appointment-bound:** the portal JWT payload carries `appointment_id` and `sub`
  (patient_id). Every portal endpoint re-validates these claims against the DB row.

---

## Portal JWT — `security.py`

```python
_PORTAL_TYPE = "portal"

create_portal_token(patient_id, appointment_id, tenant_id) → str
# payload: {sub, appointment_id, tenant_id, type="portal", iat, exp=now+30min}
```

`decode_token(token, "portal")` in `deps.get_portal_payload` raises `TokenError`
(→ 401) if the type claim is missing or wrong.  Staff access tokens (`type="access"`)
are rejected by portal endpoints; portal tokens are rejected by staff endpoints.

---

## Slot algorithm — `reschedule_service.py`

### Grid generation

```
_SLOT_MINUTES = 45
_DAY_START    = 9    # 09:00 UTC
_DAY_END      = 17   # 17:00 UTC

_candidate_slots(from_date, days_ahead):
  for each day in [from_date, from_date + days_ahead):
    slot = date + 09:00 UTC
    while slot + 45min <= date + 17:00 UTC:
      yield slot
      slot += 45min
```

Yields 11 slots per day: 09:00, 09:45, 10:30, 11:15, 12:00, 12:45, 13:30,
14:15, 15:00, 15:45, 16:15. (16:15 + 45 = 17:00 exactly, so it is included.)

### Conflict detection

```python
def _overlap(a_start, a_duration, b_start, b_duration) -> bool:
    return a_start < b_start + timedelta(minutes=b_duration) and \
           b_start < a_start + timedelta(minutes=a_duration)
```

Standard half-open interval overlap: `[a_start, a_end)` overlaps `[b_start, b_end)`
iff `a_start < b_end AND b_start < a_end`.

### DB query bounds

Fetching booked appointments with:
```
scheduled_at >= window_start - 480min   ← catches appointments that started before
scheduled_at < window_end               ← window but extend into it (max duration)
```
480 minutes is the maximum `duration_minutes` enforced by the Appointment schema.
The Python `_overlap` check then filters down to true conflicts.

### Past-slot pruning

Slots are filtered by `slot > now` after grid generation; the DB query is not
bounded by `now` because a candidate slot in the past might still conflict with
a booked appointment that extends into the future.

### `reschedule_appointment` conflict check

When rescheduling, the appointment's own current slot must not block the new slot.
The query excludes `Appointment.id != appointment_id` so if a patient tries to
re-book the exact same time, it succeeds (no artificial conflict with themselves).

### Re-reschedule (rescheduled → rescheduled)

The normal appointment status DAG in `appointment_service.py` does not include
`rescheduled → rescheduled`. `reschedule_appointment` bypasses the DAG by
setting `appt.status = "rescheduled"` directly, which is intentional: patients
may change their mind more than once. The `updated_at` onupdate fires, which
invalidates the notification worker's Redis-cached SMS body for that appointment.

---

## Waitlist — `waitlist_service.py`

### `find_waitlist_match` — matching rules

All five conditions must hold for an entry to match:

| Condition | Detail |
|-----------|--------|
| `tenant_id` | Entry must belong to the same tenant as the cancelled appointment |
| `filled_at IS NULL` | Entry must not already have been filled |
| `provider_id` | Entry `provider_id` is NULL (any provider) or equals `appointment.provider_id` |
| `preferred_times` | Entry value is `"any"`, or matches `"morning"` (hour < 12) / `"afternoon"` (hour ≥ 12) |
| `preferred_days` | Entry `preferred_days` is NULL/empty, or contains the lowercase weekday name of `scheduled_at` |

Entries are ordered by `added_at` ASC — FIFO priority. The first entry that
passes all filters is returned; the rest are not examined.

### 2-hour lead-time guard

```python
if appointment.scheduled_at - now < timedelta(hours=2):
    return None
```

Cancellations within 2 hours of the appointment time are not worth filling —
there is insufficient time for the patient to receive the SMS and travel. The
check happens before any DB query, so no entries are read in this case.

### `notify_waitlist_patient` transaction guarantee

The Twilio SMS dispatch happens before the DB commit. If the SMS succeeds but the
commit fails (e.g., DB connection lost), the task will retry on Arq's next attempt.
The patient may receive a second SMS. This is preferable to the reverse (commit
first, SMS silently dropped): a duplicate notification is annoying but recoverable;
a filled slot that was never communicated leaves the patient without notice.

### SMS body

```
Hi {first_name}, a slot opened with Dr. {last_name} on {date_str} at {time_str}.
Contact {tenant.name} to book it.
```

Hard-truncated to 160 chars at the last word boundary before char 157, then `...`
appended. Clinic staff must follow up to actually book the patient.

---

## `fill_waitlist_task` — `waitlist_worker.py`

```
fill_waitlist_task(ctx, appointment_id):
  1. Fetch Appointment — skip if not found
  2. Skip if status != "cancelled"
  3. Idempotency: SELECT appointment_events WHERE event_type="waitlist_filled"
     → already exists: return {status: "already_filled"}
  4. find_waitlist_match(db, appt) → None: return {status: "no_match"}
  5. notify_waitlist_patient(db, entry, appt, twilio_client, from_number)
     → marks entry.filled_at, creates Notification, appends "waitlist_filled" event
  6. Return {status: "notified", waitlist_entry_id, notification_id}
```

**Trigger:** `PATCH /appointments/{id}` with `status="cancelled"` calls
`arq.enqueue_job("fill_waitlist_task", appointment_id)`. `get_arq_pool` is
injected as `Depends` so tests can override it without a live Redis connection.

**Idempotency guarantee:** the `waitlist_filled` event check in step 3 prevents a
second notification even if the task is enqueued twice (e.g., staff cancels the
same appointment twice, or Arq retries after a partial failure).

---

## Edge cases handled

| Scenario | Behaviour |
|----------|-----------|
| Magic link for cancelled/completed appointment | `send_magic_link` raises `MagicLinkError` (→ 404) — only active appointments can generate links |
| Magic link used twice | Second `verify_magic_link` call finds no Redis key (deleted on first use) → 401 |
| Magic link expired (> 30 min) | Redis TTL expires; `verify_magic_link` gets `None` → 401 |
| Portal JWT used on wrong appointment | `jwt_appointment_id != appointment_id` in `reschedule_appointment` → 400 |
| Portal JWT patient mismatch | `appt.patient_id != jwt_patient_id` in `reschedule_appointment` → 400 |
| Staff access token on portal endpoint | `decode_token(token, "portal")` rejects `type="access"` → 401 |
| Reschedule to occupied slot | `_overlap` detects conflict → 400 "not available" |
| Reschedule to own current slot | Conflict query excludes `appointment.id` — succeeds (no-op reschedule) |
| Reschedule to past datetime | `new_slot <= now` guard → 400 "must be in the future" |
| Re-reschedule (rescheduled → rescheduled) | DAG bypassed intentionally; `updated_at` fires, invalidates SMS cache |
| Cancellation within 2h of appointment | `find_waitlist_match` returns `None` before DB query — no notification |
| `fill_waitlist_task` retried after partial commit | `waitlist_filled` event check → `{status: "already_filled"}`, no duplicate SMS |
| No waitlist entries match | `find_waitlist_match` returns `None` → `{status: "no_match"}` |
| Appointment not yet scored (risk_bucket null) | Waitlist matching ignores risk score entirely — orthogonal concerns |
| Slot grid boundary (16:15 UTC) | 16:15 + 45min = 17:00 exactly, so condition `slot + 45min <= 17:00` is true — slot is included |
| DB query window wider than grid | `window_start - 480min` catches long appointments that started before the window but extend into it |

---

## Open TODOs

1. **No waitlist CRUD endpoints yet.** `add_to_waitlist` / `WaitlistEntry` exist in
   the service layer but there are no `GET /waitlist`, `POST /waitlist`, or
   `DELETE /waitlist/{id}` HTTP routes. Staff cannot manage the waitlist via the API.

2. **Waitlist notification does not give patient a booking link.** The SMS says
   "contact us to book." A proper flow would generate a new magic link pointing to
   a "claim this slot" portal page, but that requires appointment creation from
   the patient side, which is out of scope for Phase 6.

3. **`send_magic_link` uses `asyncio.get_event_loop()`** (deprecated in Python 3.10+).
   Should be `asyncio.get_running_loop()`. The waitlist_service already uses the
   correct form; magic_link_service carries the stale call.

4. **`mark_delivered` Twilio status-callback endpoint not wired** (carried from Phase 5).

5. **`weather_alert` hardcoded False** (carried from Phase 4).

6. **Slot grid is UTC-only.** No per-tenant timezone support. Clinics in non-UTC
   timezones will see grid boundaries at unexpected local times. Fixing this requires
   a `timezone` column on `tenants` and converting `_DAY_START`/`_DAY_END` at query time.
