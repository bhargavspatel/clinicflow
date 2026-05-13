# LLM Prompt Strategy — ClinicFlow

## Model selection
- gpt-4o-mini   SMS reminder generation (high volume, cost-sensitive, short output)
- gpt-4o        Dashboard anomaly explanations (low volume, reasoning depth needed)

## SMS generation — system prompt
You are a friendly appointment reminder assistant for {clinic_name}.
Generate a short, warm, personalized SMS reminder.

Rules:
- Under 160 characters total (hard limit)
- Use the patient's first name
- Mention the provider name and appointment type naturally
- Include ONE clear call to action: reply YES to confirm or tap the link to reschedule
- Never use clinical jargon or alarming language
- Never include sensitive information beyond name, provider, and appointment time
- If you cannot produce a fully compliant message, return null for message

## SMS generation — user prompt
Patient first name: {patient_first_name}
Provider: Dr. {provider_last_name}
Appointment type: {appointment_type_label}
Date and time: {scheduled_at_formatted}
Reschedule link: {magic_link}

## Output schema (Pydantic v2 — enforce this on every call)
class SMSContent(BaseModel):
    message: str | None    # None triggers the rule-based fallback
    character_count: int
    compliant: bool        # model self-assessment

## Rule-based fallback (use when LLM returns None, fails, or raises any exception)
Template:
  "Hi {first_name}, reminder: appt with Dr. {last_name} on {date} at {time}.
   Reply YES to confirm or visit {link} to reschedule. – {clinic_name}"

Validation before sending:
- len(message) <= 160 characters
- If over 160, truncate at last word boundary before 157 and append "..."

## OpenAI call settings
- max_tokens:  80   (SMS cannot exceed 160 chars — 80 tokens is more than enough)
- temperature: 0.4  (enough variation to feel personal, not unpredictable)

## Caching strategy
Cache key:  sha256(patient_id + appointment_id + sequence_number)
TTL:        24 hours
Invalidate: if appointment.updated_at is newer than cache entry (patient rescheduled)

## Cost guardrails
- Log tokens consumed on every call
- Log whether fallback was triggered
- If fallback rate exceeds 5% in a 1hr window, alert via Sentry
- Never retry a failed LLM call more than once — go straight to fallback
