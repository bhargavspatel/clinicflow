# System Architecture — ClinicFlow

## Layers (top to bottom)

### Client tier
- Staff dashboard: React + Vite SPA, connects via REST + WebSocket
- Patient portal:  React SPA, stateless, accessed only via magic link
- SMS channel:     Twilio inbound webhooks POST directly to the API

### API Gateway (single FastAPI app)
- Responsibilities: JWT validation, tenant extraction, rate limiting, routing
- WebSocket endpoint: /ws — authenticates then pushes real-time events to staff

### Service layer (no direct client access)
- AppointmentService   CRUD, status transitions, calendar sync
- RiskScoringService   Pure function — no DB calls, fully unit-testable
- NotificationService  Enqueues jobs only — does not send directly
- BillingService       Stripe subscription and usage management

### Async workers (Arq + Redis)
- send_sms_task           Calls OpenAI then Twilio, handles retries
- score_appointments      Nightly batch rescorer for next 7 days
- fill_waitlist_task      Triggered when an appointment is cancelled
- sync_calendar_task      Polls Google Calendar via OAuth tokens

### Data tier
- PostgreSQL 15   Source of truth, Row-Level Security enforced per tenant
- Redis 7         Arq job queue, LLM response cache, pub/sub for WebSocket fanout
- S3 / MinIO      Patient intake PDFs, clinic logos, audit log exports

### External services
- OpenAI   GPT-4o-mini for SMS copy, GPT-4o for anomaly explanations
- Twilio   Outbound SMS + inbound reply webhooks
- Google   OAuth 2.0 calendar sync
- Stripe   Subscription billing + SMS usage metering

## Multi-tenancy strategy
Shared database with tenant_id on every row, enforced by Postgres RLS.

Every table has:
  tenant_id UUID NOT NULL REFERENCES tenants(id)

Every session runs:
  SET app.current_tenant = '<tenant_uuid>'

RLS policy on every table:
  USING (tenant_id = current_setting('app.current_tenant')::uuid)

FastAPI dependency get_current_tenant() sets this at the start of every request.

## Real-time flow
1. Patient replies YES to SMS
2. Twilio fires webhook  POST /webhooks/twilio
3. Handler updates appointment status in Postgres
4. Publishes event to Redis pub/sub: tenant:{tenant_id}:events
5. WebSocket manager reads pub/sub and pushes JSON to all connected staff

## Appointment lifecycle
Created → Scored → bucketed low/medium/high → Notification queued
→ SMS sent → Patient replies → Confirmed / Reschedule / No response
→ If reschedule: magic link → portal → new slot → waitlist auto-filled
→ Outcome written to appointment_events
→ Outcome used to improve future risk scoring weights
