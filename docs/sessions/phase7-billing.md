# Phase 7 — Billing

**Date:** 2026-05-10
**Status:** Complete
**Tests:** 190 total (185 carry-over + 5 new)

---

## What was built

Phase 7 adds Stripe subscription billing: a plan catalogue, a Checkout Session
flow, a Billing Portal for self-service, a webhook handler for subscription
lifecycle events, and a plan-gating layer that enforces SMS limits and blocks
lapsed tenants from staff endpoints.

---

## Files

| File | Status | Description |
|------|--------|-------------|
| `app/schemas/billing.py` | NEW | `PlanOut`, `PlansResponse`, `SubscribeRequest`, `CheckoutResponse`, `SubscriptionStatusOut`, `PortalRequest`, `PortalResponse` |
| `app/services/billing_service.py` | NEW | `get_plans`, `create_subscription`, `get_subscription_status`, `create_portal_session`; all Stripe I/O in thread executor |
| `app/api/v1/endpoints/billing.py` | NEW | `GET /billing/plans`, `POST /billing/subscribe`, `GET /billing/subscription`, `POST /billing/portal` — all `require_owner` |
| `app/api/v1/endpoints/webhooks.py` | MODIFIED | Added `POST /webhooks/stripe` + five private handlers + `_plan_from_price_id` helper |
| `app/core/plan_gate.py` | NEW | `PaymentRequiredError`, `check_sms_limit`, `increment_sms_usage`, `require_active_subscription` |
| `app/workers/notification_worker.py` | MODIFIED | Added `check_sms_limit` call before Twilio dispatch; `increment_sms_usage` bundled into result-persist transaction |
| `app/api/v1/__init__.py` | MODIFIED | `_gated` dependency list applied to `patients`, `providers`, `appointments` routers |
| `tests/api/test_billing.py` | NEW | 4 tests: invalid signature, subscription deleted, payment succeeded, suspended 402 |
| `tests/unit/test_plan_gate.py` | NEW | 1 test: `send_sms_task` skips at SMS limit |

---

## Billing service — `billing_service.py`

### Plan catalogue

```python
_PLANS = [
    {"id": "starter", "name": "Starter", "price_usd_cents": 14900, "sms_limit": 500,  "description": "Up to 500 SMS/month"},
    {"id": "growth",  "name": "Growth",  "price_usd_cents": 24900, "sms_limit": 2000, "description": "Up to 2000 SMS/month"},
]
```

`get_plans()` is a sync function (no I/O) that injects `stripe_starter_price_id`
and `stripe_growth_price_id` from settings into each plan dict at call time.

### Stripe call pattern

All Stripe API calls are synchronous (stripe-python v10 class-based API). Each
is dispatched via a private coroutine:

```python
async def _stripe(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))
```

`api_key` is passed per-call (keyword argument), never assigned to the module
global, so it is safe with the `@lru_cache` settings singleton.

### `create_subscription` flow

```
POST /billing/subscribe
  1. Validate price_id ∈ {starter_price_id, growth_price_id} → 400 if unknown
  2. Guard: existing Subscription.status ∈ {active, trialing} → 400
  3. _get_or_create_stripe_customer:
       - Return tenant.stripe_customer_id if already set
       - Otherwise: query owner User for email, stripe.Customer.create,
         save stripe_customer_id to tenant via db.flush()
  4. stripe.checkout.Session.create(mode="subscription", ...)
  5. db.commit()   ← persists stripe_customer_id if newly created
  6. Return session.url
```

The `Subscription` DB row is **not** created here. It is created by the
`customer.subscription.created` Stripe webhook after checkout completes.

### `create_portal_session`

Requires `tenant.stripe_customer_id` (must have subscribed at least once).
Creates a `stripe.billing_portal.Session` and returns its URL. The
`return_url` parameter defaults to `https://app.clinicflow.app/billing`.

---

## Stripe webhook — `webhooks.py`

### Request pipeline

```
POST /webhooks/stripe
  1. Read raw request body (bytes — required for HMAC)
  2. Check Stripe-Signature header present → 403 if missing
  3. stripe.Webhook.construct_event(payload, sig, secret)
       SignatureVerificationError → 400
       ValueError (malformed payload) → 400
  4. Idempotency: redis.get("stripe:event:{event_id}")
       Already processed → return {status: "already_processed"}
  5. Route to handler by event_type
  6. redis.set("stripe:event:{event_id}", "1", ex=7days)
       Written ONLY on success — failure keeps key absent so Stripe retries
  7. Return {status: "ok"}
```

### Event handlers

| Stripe event | Handler | DB mutations |
|---|---|---|
| `customer.subscription.created` | `_upsert_subscription(activate_tenant=True)` | Upsert `Subscription` row; set `tenant.plan_tier`; set `tenant.is_active = True` |
| `customer.subscription.updated` | `_upsert_subscription(activate_tenant=False)` | Upsert `Subscription` row; set `tenant.plan_tier`; leave `is_active` untouched |
| `customer.subscription.deleted` | `_handle_subscription_deleted` | `subscription.status = "canceled"`; `tenant.is_active = False` |
| `invoice.payment_failed` | `_handle_invoice_payment_failed` | `subscription.status = "past_due"` |
| `invoice.payment_succeeded` | `_handle_invoice_payment_succeeded` | `subscription.sms_usage_count = 0` |

### `_upsert_subscription` detail

Looks up tenant by `stripe_customer_id` (not by tenant_id). Maps `price_id` to
`(plan_tier, sms_limit)` via `_plan_from_price_id`; falls back to
`("starter", 500)` with a warning log for unknown price IDs. On `created`, the
Subscription row may not exist yet — the handler creates it. On `updated`, it
exists — the handler updates every field except `sms_usage_count` (preserving
the running counter for the current period).

`activate_tenant=True` is passed only for `created` so that re-activating a
lapsed tenant happens correctly on a new subscription, while an `updated` event
(e.g., a plan change while `past_due`) does not inadvertently reset `is_active`.
The `invoice.payment_succeeded` path does not touch `is_active` either; the
tenant is effectively re-enabled when their next `customer.subscription.updated`
fires with `status = "active"`.

### Invoice handler guard

Both invoice handlers check `invoice_obj.get("subscription")` before querying.
A `None` value means the invoice is for a one-time charge (not a subscription
renewal), and the handler returns immediately.

### Idempotency guarantee

The Redis key is written only after the DB commit succeeds. If the commit raises
an exception, the key is never written and Stripe will retry the webhook. On
retry, the handler processes the event again from a clean state. Duplicate
events (Stripe occasionally delivers the same event twice) are short-circuited
by the Redis check before any DB query runs.

---

## Plan gate — `plan_gate.py`

### `check_sms_limit(db, tenant_id)`

```python
# Raises PaymentRequiredError if:
#   - No Subscription row exists for the tenant
#   - sms_usage_count >= sms_usage_limit
```

Read-only. No commit. Called inside the first DB session block of
`send_sms_task` after all data is fetched. `PaymentRequiredError` is caught in
the worker and converted to a graceful early return so Arq does not count it as
a task failure and does not retry.

### `increment_sms_usage(db, tenant_id)`

Mutates `sms_usage_count += 1` without committing. Called in the second DB
session block of `send_sms_task` before `db.commit()` so the usage increment
and the notification row update commit atomically. Failed or skipped sends never
reach this call, so only successful Twilio dispatches count against the limit.

### `require_active_subscription` — FastAPI dependency

```python
async def require_active_subscription(
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
) -> None:
    sub = ...
    if sub is None:
        return          # no row → free trial, allow access
    if sub.status in {"past_due", "canceled"}:
        raise HTTPException(status_code=402, ...)
```

**No-subscription = pass through.** New tenants with no Subscription row get
unblocked access (free trial period). Only an explicitly `past_due` or
`canceled` row triggers 402. This keeps all pre-existing tests green —
they register clinics without Stripe and never hit 402.

Applied as a router-level dependency to `patients`, `providers`, and
`appointments` via `_gated = [Depends(require_active_subscription)]` in
`app/api/v1/__init__.py`. `billing`, `auth`, `webhooks`, and `portal` are
intentionally **excluded**:
- `billing` — tenants with lapsed payments must reach the portal to fix it
- `auth` — no JWT yet at registration/login
- `webhooks` — verified by HMAC, not JWT
- `portal` — uses a portal JWT (`type="portal"`), not a staff JWT; `get_current_tenant` would reject it

### SMS usage lifecycle

```
New billing period starts
  → invoice.payment_succeeded webhook → sms_usage_count = 0

Staff triggers reminder (send_sms_task):
  → check_sms_limit: pass if count < limit, raise PaymentRequiredError if count >= limit
  → [generate and dispatch SMS]
  → increment_sms_usage: count += 1  (same transaction as notification row update)

Period limit reached:
  → all subsequent send_sms_task calls skip with {skipped: true, reason: "sms_limit_reached"}
  → HTTP endpoints unaffected (require_active_subscription does not check SMS count)
```

---

## Router wiring

```python
# app/api/v1/__init__.py
_gated = [Depends(require_active_subscription)]

router.include_router(patients.router,     ..., dependencies=_gated)
router.include_router(providers.router,    ..., dependencies=_gated)
router.include_router(appointments.router, ..., dependencies=_gated)
```

FastAPI deduplicates `get_current_tenant` and `get_db` across the router
dependency and the route handler — no double JWT decode, no second DB session.

---

## Tests

### `tests/api/test_billing.py` (4 tests)

| Test | Setup | Assertion |
|------|-------|-----------|
| `test_stripe_invalid_signature_returns_400` | Mock `construct_event` raises `SignatureVerificationError` | `resp.status_code == 400`, "signature" in detail |
| `test_stripe_subscription_deleted_deactivates_tenant` | Seed active Subscription; mock `customer.subscription.deleted` event | `sub.status == "canceled"`, `tenant.is_active is False` |
| `test_stripe_payment_succeeded_resets_sms_count` | Seed Subscription with `sms_usage_count=347`; mock `invoice.payment_succeeded` | `sub.sms_usage_count == 0` |
| `test_past_due_tenant_returns_402_on_gated_endpoint` | Seed `status="past_due"` Subscription; `GET /patients` with owner JWT | `resp.status_code == 402`, "past_due" in detail |

All webhook tests inject a `get_redis` mock via `app.dependency_overrides` with
`redis.get = AsyncMock(return_value=None)` (no prior idempotency hit) and assert
DB state via `seed_db.expire_all()` after the request.

### `tests/unit/test_plan_gate.py` (1 test)

| Test | Setup | Assertion |
|------|-------|-----------|
| `test_send_sms_task_skips_when_sms_limit_reached` | Full API setup (clinic, provider, patient, appointment); Subscription with `sms_usage_count=500`, `sms_usage_limit=500`; queued Notification via `enqueue_reminder` | `result["skipped"] is True`, `result["reason"] == "sms_limit_reached"`, `twilio_client.messages.create` not called |

---

## Open TODOs

1. **`subscription.updated` does not re-activate `is_active`.** When Stripe
   resolves a `past_due` subscription back to `active`, a
   `customer.subscription.updated` event fires with `status = "active"`. The
   current `_upsert_subscription(activate_tenant=False)` does not update
   `is_active`. Tenants would remain blocked until a manual fix or a
   `customer.subscription.created` event. Fix: check `stripe_status == "active"`
   inside the `updated` handler and set `tenant.is_active = True`.

2. **No Stripe metered usage reporting.** `sms_usage_count` is tracked locally
   but never reported back to Stripe as metered usage. Overage billing is
   therefore not possible. Requires calling `stripe.SubscriptionItem.create_usage_record`
   on each SMS send if metered pricing is adopted.

3. **Checkout `success_url` and `cancel_url` are hardcoded defaults.** They
   point to `https://app.clinicflow.app/billing/*`. These should be tenant-aware
   URLs (e.g., `https://{subdomain}.clinicflow.app/billing/*`) once the frontend
   is live (Phase 8).

4. **`mark_delivered` Twilio status-callback endpoint not wired** (carried from Phase 5).

5. **`weather_alert` hardcoded False** (carried from Phase 4).

6. **No waitlist CRUD endpoints** (carried from Phase 6).
