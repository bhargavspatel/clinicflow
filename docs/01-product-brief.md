# Product Brief — ClinicFlow

## Problem
Independent medical clinics (physical therapy, dental, dermatology, chiropractic)
with 2-10 providers suffer no-show rates of 15-22%. Each empty 45-minute slot
costs ~$120 in lost revenue. Existing solutions are:
- Dumb: generic SMS blast tools, low engagement, one-size-fits-all
- Expensive: Luma Health / Weave built for larger practices at $400+/month
- Manual: hiring a part-time admin to call patients — inconsistent and costly

The 250,000 independent specialty clinic market in the US is underserved.

## Solution
ClinicFlow predicts which patients are likely to no-show, intervenes
intelligently before the appointment, and makes rescheduling frictionless.

## Target customer
- 4-provider physical therapy clinic in suburban US
- 18% no-show rate, losing ~$2,000/month in empty slots
- Uses Google Calendar or a basic EHR (not Epic / Athenahealth)
- Owner is decision-maker; 1-2 front-desk staff
- Would pay $150-300/month per location for measurable ROI

## Success metrics
- No-show rate reduced from ~18% to under 10%
- Waitlist fill rate above 60% of recovered slots
- SMS confirmation rate above 50%
- Clinic owner can attribute dollar savings directly to ClinicFlow

## User roles and needs
| Role       | Primary need                                             |
|------------|----------------------------------------------------------|
| Owner      | Billing, team management, ROI dashboard                  |
| Provider   | Own schedule, patient history, risk flags                |
| Front-desk | All appointments, send manual reminders, manage waitlist |
| Patient    | Confirm or reschedule their appointment with one tap     |

## MVP scope (Phases 1-7)
- Manual appointment entry plus Google Calendar sync in Phase 3
- Rule-based risk scoring (ML model added later with real data)
- SMS reminders via Twilio with LLM-personalized copy
- Patient magic-link reschedule portal
- Waitlist auto-fill when appointment is cancelled
- Stripe subscription billing
- Staff dashboard with real-time appointment status via WebSockets

## Out of scope for MVP
- HIPAA certification (architecture is designed for it, not certified)
- Athena or DrChrono FHIR integration
- Native mobile app
- Multi-location UI (data model supports it, UI does not yet)

## Pricing tiers
- Starter: $149/month — up to 500 SMS/month, up to 3 providers
- Growth:  $249/month — up to 2000 SMS/month, unlimited providers
- SMS overage: $0.05 per SMS above the monthly limit
