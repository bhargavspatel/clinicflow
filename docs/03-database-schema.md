# Database Schema — ClinicFlow

All tables:
- UUID primary keys using gen_random_uuid()
- created_at TIMESTAMPTZ DEFAULT now()
- updated_at TIMESTAMPTZ (maintained by app layer)
- tenant_id UUID NOT NULL REFERENCES tenants(id)  (all tables except tenants itself)
- RLS policy applied to every table except tenants

---

## tenants
| Column              | Type         | Notes                               |
|---------------------|--------------|-------------------------------------|
| id                  | UUID PK      |                                     |
| name                | TEXT         | "Sunrise Physical Therapy"          |
| subdomain           | TEXT UNIQUE  | "sunrise-pt"                        |
| stripe_customer_id  | TEXT         | nullable until subscribed           |
| plan_tier           | TEXT         | starter / growth / pro              |
| is_active           | BOOLEAN      | false = suspended (payment failed)  |

## users
| Column        | Type     | Notes                               |
|---------------|----------|-------------------------------------|
| id            | UUID PK  |                                     |
| tenant_id     | UUID FK  |                                     |
| email         | TEXT     | unique per tenant                   |
| password_hash | TEXT     | bcrypt                              |
| role          | TEXT     | owner / provider / front_desk       |
| full_name     | TEXT     |                                     |
| is_active     | BOOLEAN  |                                     |

## refresh_tokens
| Column      | Type         | Notes                         |
|-------------|--------------|-------------------------------|
| id          | UUID PK      |                               |
| user_id     | UUID FK      |                               |
| tenant_id   | UUID FK      |                               |
| token_hash  | TEXT UNIQUE  | SHA-256 of raw token          |
| expires_at  | TIMESTAMPTZ  |                               |
| revoked_at  | TIMESTAMPTZ  | null = still valid            |

## patients
| Column     | Type     | Notes                          |
|------------|----------|--------------------------------|
| id         | UUID PK  |                                |
| tenant_id  | UUID FK  |                                |
| full_name  | TEXT     |                                |
| phone      | TEXT     | E.164 format +1xxxxxxxxxx      |
| email      | TEXT     | nullable                       |
| dob        | DATE     | nullable                       |
| notes      | TEXT     | free text, internal only       |

## providers
| Column     | Type     | Notes                          |
|------------|----------|--------------------------------|
| id         | UUID PK  |                                |
| tenant_id  | UUID FK  |                                |
| user_id    | UUID FK  | links to users table           |
| specialty  | TEXT     | "Physical Therapy"             |
| is_active  | BOOLEAN  |                                |

## appointments
| Column            | Type         | Notes                                           |
|-------------------|--------------|-------------------------------------------------|
| id                | UUID PK      |                                                 |
| tenant_id         | UUID FK      |                                                 |
| patient_id        | UUID FK      |                                                 |
| provider_id       | UUID FK      |                                                 |
| scheduled_at      | TIMESTAMPTZ  |                                                 |
| duration_minutes  | INT          | default 45                                      |
| appointment_type  | TEXT         | initial_eval / follow_up / consultation         |
| status            | TEXT         | scheduled/confirmed/rescheduled/completed/no_show/cancelled |
| risk_score        | INT          | 0-100, null until scored                        |
| risk_bucket       | TEXT         | low / medium / high, null until scored          |
| last_scored_at    | TIMESTAMPTZ  |                                                 |
| notes             | TEXT         |                                                 |

Indexes: (tenant_id, scheduled_at), (tenant_id, status), (patient_id)

## notifications
| Column           | Type         | Notes                                     |
|------------------|--------------|-------------------------------------------|
| id               | UUID PK      |                                           |
| tenant_id        | UUID FK      |                                           |
| appointment_id   | UUID FK      |                                           |
| channel          | TEXT         | sms / email                               |
| direction        | TEXT         | outbound / inbound                        |
| body             | TEXT         | message content                           |
| twilio_sid       | TEXT         | Twilio message SID                        |
| status           | TEXT         | queued/sent/delivered/failed/received     |
| sent_at          | TIMESTAMPTZ  |                                           |
| delivered_at     | TIMESTAMPTZ  |                                           |

## appointment_events  (append-only — never update or delete)
| Column          | Type         | Notes                                    |
|-----------------|--------------|------------------------------------------|
| id              | UUID PK      |                                          |
| tenant_id       | UUID FK      |                                          |
| appointment_id  | UUID FK      |                                          |
| event_type      | TEXT         | created/scored/reminded/confirmed/       |
|                 |              | rescheduled/completed/no_show/waitlist_filled |
| actor_id        | UUID         | user_id or null for system events        |
| metadata        | JSONB        | e.g. {"risk_score": 72, "bucket": "high"}|
| occurred_at     | TIMESTAMPTZ  | DEFAULT now()                            |

## waitlist
| Column           | Type         | Notes                          |
|------------------|--------------|--------------------------------|
| id               | UUID PK      |                                |
| tenant_id        | UUID FK      |                                |
| patient_id       | UUID FK      |                                |
| provider_id      | UUID FK      | null = any provider            |
| preferred_days   | TEXT[]       | ["monday","wednesday"]         |
| preferred_times  | TEXT         | morning / afternoon / any      |
| added_at         | TIMESTAMPTZ  |                                |
| filled_at        | TIMESTAMPTZ  | null = still waiting           |

## subscriptions
| Column                  | Type         | Notes                        |
|-------------------------|--------------|------------------------------|
| id                      | UUID PK      |                              |
| tenant_id               | UUID FK      | UNIQUE                       |
| stripe_subscription_id  | TEXT UNIQUE  |                              |
| stripe_price_id         | TEXT         |                              |
| status                  | TEXT         | active / past_due / cancelled|
| current_period_end      | TIMESTAMPTZ  |                              |
| sms_usage_count         | INT          | resets each billing cycle    |
| sms_usage_limit         | INT          | 500 starter / 2000 growth    |
