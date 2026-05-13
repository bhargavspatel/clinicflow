# Tech Decisions — ClinicFlow

## FastAPI over Django or Flask
- Native async is essential for WebSockets and concurrent LLM/Twilio calls
- Pydantic v2 validation is built in — request validation is automatic
- OpenAPI docs are generated automatically, useful during frontend development
- Flask is sync-first; Django async is bolted on and less ergonomic

## Arq over Celery
- Arq is async-native — Celery is synchronous with async added later
- Redis is already in the stack — no additional broker like RabbitMQ needed
- Simpler codebase and easier local debugging at this project scale

## Postgres RLS over application-level tenant filtering
- Tenant isolation enforced at the database level — cannot be bypassed by an app bug
- Single connection pool — no per-tenant connection overhead
- Tradeoff: migrations and raw queries require care; SQLAlchemy abstracts most of it

## Shared DB over schema-per-tenant or DB-per-tenant
- Schema-per-tenant: N schemas to migrate on every release — operational nightmare
- DB-per-tenant: expensive, heavy, justified only above ~1000 large tenants
- Shared DB + RLS is the standard SaaS pattern used by Notion, Linear, and others

## gpt-4o-mini for SMS reminders
- Approximately 20x cheaper than gpt-4o per token
- SMS copy is a low-complexity generation task — short, templated, constrained
- Reserve gpt-4o for tasks needing multi-step reasoning (anomaly detection, reports)

## Twilio over AWS SNS
- Inbound SMS handling (reply webhooks) is first-class in Twilio
- 10DLC registration for US business SMS is supported natively
- Better deliverability documentation and debugging tools at small scale

## Magic links for patient auth over passwords
- Patients interact infrequently — they will not remember a password
- No credential storage on the patient side — reduces attack surface
- Short-lived signed token sent via SMS is sufficient for reschedule-only access

## Arq workers for risk scoring, not inline in the API request
- Scoring 200 appointments takes 0.5-2 seconds per batch — unacceptable in API response
- Nightly batch keeps all scores fresh before the day begins
- Manual rescore endpoint enqueues a priority job rather than scoring inline
