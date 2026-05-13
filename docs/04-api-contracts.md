# API Contracts — ClinicFlow

Base URL:    /api/v1
Auth header: Authorization: Bearer <access_token>
Response envelope:
  Success: { "data": <payload> }
  Error:   { "error": { "code": "...", "message": "..." } }

---

## Auth  (no authentication required)
POST  /auth/register           Create tenant + owner user
POST  /auth/login              Email + password → access_token + refresh_token
POST  /auth/refresh            Rotate refresh token → new pair
POST  /auth/logout             Revoke refresh token
POST  /auth/magic-link/send    Send magic link SMS to patient
GET   /auth/magic-link/verify  Validate token → short-lived patient JWT

## Tenants  (owner only)
GET   /tenants/me              Current tenant info + plan details
PATCH /tenants/me              Update clinic name or subdomain

## Users  (owner only)
GET    /users                  List staff for this tenant
POST   /users                  Invite new staff member (sends email)
PATCH  /users/{id}             Update role or deactivate
DELETE /users/{id}             Soft delete

## Patients
GET    /patients               List paginated, search by name or phone
POST   /patients               Create patient
GET    /patients/{id}          Detail view + appointment history
PATCH  /patients/{id}          Update patient info
GET    /patients/{id}/risk-history  Historical risk scores over time

## Providers
GET    /providers              List active providers
POST   /providers              Create and link to a user account
PATCH  /providers/{id}         Update specialty or status

## Appointments
GET    /appointments                 List with filters: date range, status, provider, risk_bucket
POST   /appointments                 Create appointment
GET    /appointments/{id}            Detail including event log
PATCH  /appointments/{id}            Update status or notes
POST   /appointments/{id}/score      Manually trigger risk rescore
POST   /appointments/{id}/remind     Manually send SMS reminder now
GET    /appointments/{id}/events     Full audit event log

## Notifications
GET    /notifications          List for tenant, filter by appointment or status

## Waitlist
GET    /waitlist               List waiting patients
POST   /waitlist               Add patient to waitlist
DELETE /waitlist/{id}          Remove from waitlist

## Billing  (owner only)
GET    /billing/plans          Available Stripe plans with features
POST   /billing/subscribe      Create Stripe subscription → checkout URL
GET    /billing/subscription   Current status + SMS usage this period
POST   /billing/portal         Stripe customer portal session URL

## Webhooks  (no auth — verified by signature header)
POST   /webhooks/stripe        Stripe event handler (idempotent)
POST   /webhooks/twilio        Inbound SMS reply handler (idempotent)

## WebSocket
WS     /ws                    Authenticated staff connection
  Events pushed:
    appointment.status_changed
    appointment.scored
    notification.sent
    notification.received
    waitlist.filled

## System
GET    /health                 { "status": "ok", "db": "ok", "redis": "ok" }
