# Phase 5 — Notifications

**Date:** 2026-05-10
**Status:** Complete
**Tests:** 172 total (163 carry-over + 9 new)

---

## What was built

Phase 5 adds the full outbound SMS reminder pipeline and inbound reply handling:
a pure-function notification service, an LLM-backed SMS generation worker with
rule-based fallback, an hourly reminder scheduling cron, a Twilio inbound webhook,
and Redis caching for generated messages.

---

## Files

| File | Status | Description |
|------|--------|-------------|
| `app/services/notification_service.py` | NEW | `enqueue_reminder`, `mark_delivered`, `handle_inbound_reply` |
| `app/workers/notification_worker.py` | NEW | `send_sms_task` — OpenAI generation, Twilio dispatch, Redis cache |
| `app/workers/scheduling_worker.py` | NEW | `schedule_reminders` — hourly cron, per-bucket trigger windows |
| `app/api/v1/endpoints/webhooks.py` | NEW | `POST /webhooks/twilio` — signature verify, TwiML response |
| `app/core/redis.py` | NEW | Lazy Redis pool for the API process |
| `app/workers/main.py` | MODIFIED | Added OpenAI/Twilio clients to startup; registered `schedule_reminders`, `send_sms_task` |
| `app/api/v1/__init__.py` | MODIFIED | Registered `webhooks.router` |
| `tests/unit/test_notification_worker.py` | NEW | 3 unit tests (LLM path, exception fallback, oversized fallback) |
| `tests/api/test_webhooks.py` | NEW | 5 integration tests (signature, YES reply, TwiML shape, idempotency) |
| `tests/unit/test_scheduling_worker.py` | NEW | 1 integration test (duplicate suppression) |

---

## Service layer — `notification_service.py`

### `enqueue_reminder(db, appointment_id, sequence_number) → Notification`

- No `tenant_id` parameter — called by the system worker across all tenants;
  `tenant_id` is read from the appointment row.
- Creates `Notification(status="queued", body="", direction="outbound")`.
  Body is intentionally blank — the worker fills it after LLM generation.
- Appends `appointment_events(event_type="reminded", metadata={sequence_number, notification_id})`.
  The `sequence_number` in metadata is the idempotency signal used by
  `schedule_reminders` to detect duplicates (no dedicated column on `notifications`).
- Commits and returns the notification with its ID populated.

### `mark_delivered(db, notification_id, twilio_sid) → Notification`

- Called from the Twilio status-callback webhook (not yet wired — Phase 9 or on-demand).
- Sets `twilio_sid`, `status="delivered"`, `delivered_at=now()`.
- Assumes the worker has already set `body`, `status="sent"`, and `sent_at` before
  the delivery callback fires.

### `handle_inbound_reply(db, from_number, body) → InboundReplyResult`

- **Cross-tenant phone lookup** — Twilio inbound webhooks carry no tenant context.
  Queries `patients.phone = from_number` across all tenants.
- If multiple patients share a phone across tenants, picks the one with the
  nearest upcoming active appointment.
- **Intent parsing** (regex, no LLM):
  - YES/yes/Y/yep/yeah/confirm/1 → `"confirmed"`
  - reschedule/cancel/no/nope/can't → `"reschedule_requested"`
  - Anything else → `"unrecognized"`
- On `confirmed` intent + appointment in `scheduled` or `rescheduled` status:
  transitions appointment to `"confirmed"`, appends audit event.
- On `reschedule_requested`: records intent in the inbound notification row but
  does **not** mutate the appointment — a real reschedule requires a new
  `scheduled_at`, which only the magic-link portal flow can provide (Phase 6).
- Always creates an inbound `Notification(direction="inbound", status="received")`
  row regardless of intent.
- Raises `NoActiveAppointmentError` if phone unknown or no upcoming appointment found.

---

## Notification worker — `notification_worker.py`

### SMS generation with LLM + fallback

```
send_sms_task(ctx, notification_id):
  1. Fetch Notification + Appointment + Patient + Provider → User + Tenant
  2. Idempotency: if notification.status not in (queued, failed) → skip
  3. Retrieve sequence_number from appointment_events via JSONB query
  4. Check Redis cache (key = sha256(patient_id + appointment_id + seq))
     - Hit + appointment.updated_at unchanged → reuse cached message
     - Stale (appointment rescheduled) or miss → call OpenAI
  5. OpenAI call (max 1 attempt): gpt-4o-mini, max_tokens=80, temperature=0.4
     Validate with SMSContent schema (message, character_count, compliant)
     → fallback if: exception / message=None / compliant=False / len>160
  6. _truncate_to_160: hard 160-char enforcement, word-boundary truncation
  7. Twilio dispatch (sync client in asyncio.run_in_executor)
  8. Update notification: body, twilio_sid, status="sent", sent_at
  9. Return {notification_id, model_used, tokens_used, fallback_triggered, twilio_sid}
```

### Fallback design

The rule-based fallback template (from docs/06) fires on **any** of these conditions:
- OpenAI raises any exception (network error, timeout, rate limit, etc.)
- LLM returns `message: null`
- `compliant: false` in the LLM output
- `len(message) > 160` even when `compliant: true`

The fallback is never retried — the LLM gets exactly one attempt, then the
template runs. This matches docs/06: "Never retry a failed LLM call more than
once — go straight to fallback."

Template (docs/06):
```
Hi {first_name}, reminder: appt with Dr. {last_name} on {date} at {time}.
Reply YES to confirm or visit {link} to reschedule. – {clinic_name}
```

**Important:** `_generate_sms_body` returns the raw fallback string, which can
exceed 160 chars when the magic link is long. `_truncate_to_160` is applied by
`send_sms_task` after generation — the two concerns are intentionally separated.

### Redis caching

- **Key:** `sha256(str(patient_id) + str(appointment_id) + str(sequence_number))`
  — matches docs/06 spec exactly; same sequence → same cache entry.
- **TTL:** 24 hours.
- **Invalidation:** cache entry stores `appointment_updated_at` (ISO string).
  On retrieval, if `appointment.updated_at` has changed (patient rescheduled
  since the message was cached), the entry is discarded and the LLM is called again.
- **Value schema:** `{"message": "...", "appointment_updated_at": "<iso>"}`.
- Cache is populated after any LLM call (including fallback) so subsequent
  retries of the same notification reuse the generated message.

### Twilio dispatch

Uses the sync `twilio.rest.Client` via `asyncio.run_in_executor` rather than
Twilio's async HTTP client (`AsyncTwilioHttpClient`). This avoids managing
an `aiohttp` session lifecycle on the worker. The executor approach is
sufficient for the expected volume.

### Worker startup

`workers/main.py` startup creates and stores in `ctx`:
- `ctx["openai_client"]` → `AsyncOpenAI` (closed in shutdown)
- `ctx["twilio_client"]` → `TwilioClient` (sync, no explicit close needed)

Clients are created once at worker boot and reused across all task invocations,
avoiding per-call connection overhead.

---

## Scheduling worker — `scheduling_worker.py`

### Reminder schedule by risk bucket

| Bucket | Sequences | Trigger times |
|--------|-----------|---------------|
| low | 1 | T − 24 hr |
| medium | 2 | T − 48 hr, T − 24 hr |
| high | 3 | T − 72 hr, T − 48 hr, T − 24 hr |

Sequence numbers are 1-based in list order (seq 1 = furthest trigger).

### Hourly cron logic

Runs at `:00` every hour via `cron(schedule_reminders, minute=0)`.

1. **DB query:** Appointments with `scheduled_at ∈ [now+24h, now+73h)`, active
   status, non-null `risk_bucket`. Upper bound derivation: furthest trigger is
   72 hr (high seq-1); for that trigger to fall in `[now, now+1h)`, the
   appointment must be in `[now+72h, now+73h)` — union across all sequences
   gives `[now+24h, now+73h)` as the exact scan window.
2. **Per-sequence check:** For each appointment × sequence, compute
   `trigger_time = scheduled_at - hours_before`. If not in `[now, now+1h)`,
   skip.
3. **Idempotency:** Before creating anything, queries `appointment_events` for
   an existing `event_type="reminded"` row with
   `event_metadata->>'sequence_number' = str(seq_num)` (JSONB `.astext`).
   Match → `skipped += 1`, continue. No match → proceed.
4. **Enqueue:** `enqueue_reminder(db, appt.id, seq_num)` creates the
   `Notification` row and `appointment_events` row in one session.
   Then `ctx["redis"].enqueue_job("send_sms_task", notif.id)` dispatches
   the Arq task (`ctx["redis"]` is `ArqRedis` which has `enqueue_job`).

### Forward-looking only

The window is strictly `[now, now+1h)` — no catch-up after downtime. If the
worker is down for an hour and misses a trigger window, those reminders are not
sent. This is intentional: a flood of delayed reminders after recovery is worse
than silence for a clinical scheduling context.

---

## Twilio webhook — `webhooks.py`

### `POST /api/v1/webhooks/twilio`

1. **Parse body:** `await request.form()` gets the complete dict of Twilio-sent
   fields. All fields are needed for `RequestValidator.validate()` HMAC check —
   only capturing declared `Form(...)` parameters would drop extras and break
   validation.
2. **Signature check:** `RequestValidator(auth_token).validate(url, params, sig)`.
   Failure or missing header → HTTP 403. This is the only 4xx the webhook returns
   — Twilio does not retry 403, which is correct for invalid requests.
3. **Idempotency:** Redis key `twilio:inbound:{MessageSid}`, 24 hr TTL. Cache
   hit → return stored TwiML immediately without hitting the DB.
4. **Service call:** `handle_inbound_reply()`. Always HTTP 200 — non-200 causes
   Twilio automatic retries.
5. **TwiML responses by intent:**
   - `confirmed` → "Your appointment is confirmed! … Reply RESCHEDULE anytime."
   - `reschedule_requested` → "Tap the link in your reminder. Not changed yet."
   - `unrecognized` → "Reply YES to confirm or tap the reschedule link."
   - `NoActiveAppointmentError` → generic "couldn't find appointment" (not cached)
   - Unexpected exception → generic error message (not cached, Twilio can retry)
6. **Only successful responses are cached.** Error responses are not, so a
   retry from Twilio can succeed if the underlying issue resolves.

**Note:** `include_in_schema=False` hides the endpoint from OpenAPI docs.
The endpoint is at `/api/v1/webhooks/twilio` — configure Twilio's messaging
webhook to this URL.

### Proxy / TLS caveat

`str(request.url)` is used for signature validation. Behind a TLS-terminating
reverse proxy, this will show `http://` while Twilio signs against `https://`.
Fix at the proxy layer: rewrite `X-Forwarded-Proto` → scheme, or configure
Starlette's `TrustedHostMiddleware` + `ProxyHeadersMiddleware`.

---

## Test coverage

### `tests/unit/test_notification_worker.py` (3 tests)

| Test | What it verifies |
|------|-----------------|
| `test_llm_generates_valid_sms_under_160_chars` | LLM returns compliant ≤160-char JSON → `fallback=False`, body used as-is |
| `test_fallback_on_openai_exception` | `side_effect=Exception(...)` → `fallback=True`, body contains name/template keywords, `tokens=0` |
| `test_fallback_on_message_over_160_chars` | LLM returns 200-char string (compliant=True) → `len > 160` guard triggers fallback, `tokens` reported |

### `tests/api/test_webhooks.py` (5 tests)

| Test | What it verifies |
|------|-----------------|
| `test_invalid_twilio_signature_returns_403` | validator returns False → 403 |
| `test_missing_signature_header_returns_403` | no header → 403 (missing-header branch) |
| `test_yes_reply_confirms_appointment` | real DB + mock sig + mock Redis → 200 TwiML "confirmed" + appointment row status="confirmed" |
| `test_yes_reply_returns_twiml_xml` | XML structure (`<?xml`, `<Response>`, `<Message>`) |
| `test_duplicate_message_sid_returns_cached_twiml` | Redis returns cached bytes → response contains "cached" without hitting DB |

### `tests/unit/test_scheduling_worker.py` (1 test)

| Test | What it verifies |
|------|-----------------|
| `test_duplicate_reminder_not_enqueued` | Appointment at `now+24.5h` (24hr trigger in window), scored; first `schedule_reminders` call → 1 notification + 1 `enqueue_job`; second call → `skipped=1`, `enqueue_job` count unchanged, still 1 notification row |

---

## Open questions / TODOs

1. **Magic link placeholder:** `send_sms_task` generates
   `https://{subdomain}.clinicflow.app/reschedule/{appt.id}` as a placeholder.
   Phase 6 must replace this with a real signed token via the patient portal
   magic-link generator. The `# TODO` comment is in `notification_worker.py`.

2. **`mark_delivered` webhook not wired:** `notification_service.mark_delivered`
   is implemented and tested (unit-level), but the Twilio status-callback
   endpoint that calls it does not exist yet. The `webhooks.py` file currently
   only handles inbound SMS. A separate `POST /webhooks/twilio/status` handler
   (or a query-param branch) needs to be added.

3. **`weather_alert` hardcoded False:** Carried from Phase 4. The
   `score_appointment` service passes `weather_alert=False` to `calculate_risk`.
   Integrating a real weather-alert data source is a Phase 5+ concern.

4. **`confirmed_at` approximation:** The `-20 confirmed-reminder subtraction` in
   risk scoring approximates "confirmed a previous reminder within 48hrs" as any
   prior appointment with `status == "confirmed"`. Exact implementation requires
   a `confirmed_at` timestamp column on `appointments`.

5. **Sentry fallback-rate alerting (docs/06):** The docs specify: "If fallback
   rate exceeds 5% in a 1hr window, alert via Sentry." The worker logs
   `fallback_triggered=True/False` on every call but does not aggregate or alert.
   Implementing the Sentry integration is a Phase 9 (Polish) task.

6. **Proxy headers for Twilio signature validation:** `str(request.url)` breaks
   behind a TLS-terminating proxy. Needs `ProxyHeadersMiddleware` or a
   `WEBHOOK_BASE_URL` config setting before production deployment.

7. **`reschedule_requested` reply has no follow-up:** When a patient replies
   "reschedule", the TwiML says "tap the link in your reminder." If the original
   reminder message was truncated and the link is missing, the patient has no
   path. Phase 6 (patient portal) resolves this by ensuring magic links are
   always valid and short.
