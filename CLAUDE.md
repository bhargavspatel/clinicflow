# ClinicFlow — Project Context for Claude Code

## What this is
ClinicFlow is a multi-tenant SaaS that helps independent medical clinics reduce
patient no-shows using AI-powered risk scoring and personalized SMS reminders.
Target customer: independent specialty clinics with 2-10 providers.

## Tech stack (do not deviate without asking me first)
- Backend:     Python 3.11, FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2
- Frontend:    React 18 + Vite, TypeScript, TailwindCSS, shadcn/ui
- Database:    PostgreSQL 15 with Row-Level Security for tenant isolation
- Cache/Queue: Redis 7
- Workers:     Arq (async Python task queue — NOT Celery)
- Auth:        JWT access 15min + refresh 7 days rotated, magic links for patients
- Payments:    Stripe Billing subscriptions + metered SMS usage
- SMS:         Twilio outbound reminders + inbound webhook for replies
- LLM:         OpenAI API — gpt-4o-mini for reminders, gpt-4o for analysis only
- Storage:     AWS S3 (MinIO locally via Docker)
- Real-time:   WebSockets via FastAPI native support
- Containers:  Docker Compose locally, Render or Railway for staging

## Architectural principles (enforce in every file you touch)
1. Multi-tenancy via shared DB + tenant_id on every table + Postgres RLS
2. Every authenticated request carries tenant_id in the JWT claim
3. Long-running work goes to Arq workers — never blocks the API response
4. LLM output is always validated with Pydantic + has a rule-based fallback
5. Every state change is appended to appointment_events for full audit trail
6. Idempotency keys on ALL webhook handlers (Stripe, Twilio)
7. Cost-conscious: cache LLM responses in Redis, batch nightly scoring

## User roles (RBAC)
- owner       Full access, billing, user management
- provider    Own appointments and patients, read-only on others
- front_desk  All appointments and patients, no billing, no user management
- patient     Portal only — reschedule their own appointment via magic link

## Code style
- Type hints on every function signature (mypy strict)
- Pydantic v2 models for all API request/response shapes
- async/await throughout the entire backend
- Services layer has zero FastAPI imports — pure Python business logic
- One module = one responsibility
- No raw SQL strings — SQLAlchemy ORM or Core expressions only
- Secrets via environment variables + pydantic-settings, never hardcoded
- Raise HTTPException in routes only, raise domain exceptions in services

## Testing rules
- Tests required for: risk scoring, billing webhooks, auth flows, tenant isolation
- Pytest + pytest-asyncio + httpx AsyncClient
- Fixtures in conftest.py: test DB with RLS bypass, tenant factory, user factory
- Never write a test that only asserts a mock was called — assert actual output

## What NOT to do
- Do NOT add dependencies without flagging them to me first
- Do NOT bypass tenant scoping for any reason
- Do NOT write placeholder implementations silently — mark TODO loudly
- Do NOT generate Alembic migrations without my review
- Do NOT use synchronous SQLAlchemy in async context

## Folder layout
backend/app/
  api/v1/endpoints/   FastAPI route handlers (thin — delegate to services)
  services/           Business logic (no FastAPI imports)
  models/             SQLAlchemy ORM models
  schemas/            Pydantic v2 request/response schemas
  workers/            Arq task definitions
  core/               config.py, database.py, security.py, deps.py
  tests/              Mirrors app/ structure

frontend/src/
  components/   Reusable UI components
  pages/        Route-level page components
  hooks/        Custom React hooks
  lib/          API client, auth helpers, utils

## Reference docs (read the relevant one before starting any feature)
- docs/01-product-brief.md        What we are building and for whom
- docs/02-architecture.md         System architecture and data flow
- docs/03-database-schema.md      Full ER model with every table and column
- docs/04-api-contracts.md        All endpoint definitions
- docs/05-risk-scoring-rubric.md  Exact scoring algorithm — implement verbatim
- docs/06-llm-prompts.md          Prompt templates, output schemas, fallback rules
- docs/07-tech-decisions.md       Why we chose each technology

## Build phases
Phase 1  Foundation: Docker, FastAPI skeleton, Postgres + Redis, health check
Phase 2  Auth: tenants, RLS, JWT login/refresh, roles, middleware
Phase 3  Core models: patients, providers, appointments, CRUD APIs
Phase 4  Risk scoring: service, tests, nightly Arq batch job
Phase 5  Notifications: Arq worker, Twilio, OpenAI SMS generation
Phase 6  Patient portal: magic link auth, reschedule flow, waitlist
Phase 7  Billing: Stripe subscriptions, webhooks, plan gating
Phase 8  Frontend: React dashboard, WebSocket live updates
Phase 9  Polish: monitoring, Sentry, audit logs, CI/CD, deployment

## Current phase
Phase 1 — Foundation

## How to start every session
Say: "Read CLAUDE.md and the latest file in docs/sessions/. Summarize what
was last built, then propose what to do next. Do NOT generate code until I
approve the plan."
