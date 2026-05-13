# ClinicFlow

AI-powered no-show prevention for independent medical clinics.

## Quick start (local dev)

```bash
# 1. Copy environment file and fill in your keys
cp backend/.env.example backend/.env

# 2. Start all services
docker compose up -d

# 3. Confirm the API is running
curl http://localhost:8000/health
```

Services running after `docker compose up`:
| Service       | URL                          |
|---------------|------------------------------|
| API           | http://localhost:8000        |
| API docs      | http://localhost:8000/docs   |
| MinIO console | http://localhost:9001        |
| Postgres      | localhost:5432               |
| Redis         | localhost:6379               |

## Docs
See the `docs/` folder for full architecture, schema, and build plan.
Start with `CLAUDE.md` for the complete project context.

## Build phases
Phase 1  ✅ Foundation (current)
Phase 2  🔲 Auth
Phase 3  🔲 Core models
Phase 4  🔲 Risk scoring
Phase 5  🔲 Notifications
Phase 6  🔲 Patient portal
Phase 7  🔲 Billing
Phase 8  🔲 Frontend
Phase 9  🔲 Polish
