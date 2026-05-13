"""Risk scoring service — pure function, zero database calls.

The caller fetches all required data and passes it in as typed Pydantic models.
This makes the scorer fully deterministic and unit-testable without a database.

Input types:     AppointmentInput, PatientHistory, ScoringContext
Output type:     RiskResult

Scoring rubric:  docs/05-risk-scoring-rubric.md — implemented verbatim.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal, Optional

from pydantic import BaseModel


# ── Input schemas ─────────────────────────────────────────────────────────────

class PastAppointment(BaseModel):
    scheduled_at: datetime
    status: str
    completed_at: Optional[datetime] = None


class AppointmentInput(BaseModel):
    scheduled_at: datetime
    appointment_type: str
    duration_minutes: int


class PatientHistory(BaseModel):
    past_appointments: list[PastAppointment]
    lifetime_completed: int
    lifetime_no_shows: int
    has_phone: bool


class ScoringContext(BaseModel):
    now: datetime
    weather_alert: bool


# ── Output schema ─────────────────────────────────────────────────────────────

class RiskResult(BaseModel):
    score: int
    bucket: Literal["low", "medium", "high"]
    factors: list[str]
    scored_at: datetime


# ── Scoring function ──────────────────────────────────────────────────────────

_SECS_PER_DAY = 86_400


def calculate_risk(
    appointment: AppointmentInput,
    patient_history: PatientHistory,
    context: ScoringContext,
) -> RiskResult:
    score = 0
    factors: list[str] = []

    # ── ADDITIONS ────────────────────────────────────────────────────────────

    # Rules 1 & 2 — no-shows in past 90 days (mutually exclusive)
    cutoff_90 = context.now - timedelta(days=90)
    recent_no_shows = sum(
        1
        for a in patient_history.past_appointments
        if a.status == "no_show" and a.scheduled_at >= cutoff_90
    )
    if recent_no_shows >= 2:
        score += 30
        factors.append("2+ no-shows in past 90 days (+30)")
    elif recent_no_shows == 1:
        score += 20
        factors.append("1 no-show in past 90 days (+20)")

    # Rule 3 — booked more than 14 days in advance
    lead_seconds = (appointment.scheduled_at - context.now).total_seconds()
    if lead_seconds > 14 * _SECS_PER_DAY:
        score += 15
        factors.append("Booked more than 14 days in advance (+15)")

    # Rule 4 — high-risk time slot
    weekday = appointment.scheduled_at.weekday()  # 0 = Monday, 4 = Friday
    hour = appointment.scheduled_at.hour
    if (weekday == 0 and 8 <= hour < 10) or (weekday == 4 and hour >= 16):
        score += 10
        factors.append("High-risk time slot: Monday 8–10am or Friday after 4pm (+10)")

    # Rule 5 — no phone number on file
    if not patient_history.has_phone:
        score += 10
        factors.append("No phone number on file — SMS reminders unavailable (+10)")

    # Rule 6 — first-ever appointment (no history at all)
    if not patient_history.past_appointments:
        score += 8
        factors.append("First-ever appointment with this clinic (+8)")

    # Rule 7 — active severe weather alert
    if context.weather_alert:
        score += 15
        factors.append("Active severe weather alert (+15)")

    # Rule 8 — initial evaluation appointment type
    if appointment.appointment_type == "initial_eval":
        score += 5
        factors.append("Appointment type: initial evaluation (+5)")

    # ── SUBTRACTIONS ─────────────────────────────────────────────────────────

    # Rule S1 — patient previously confirmed a reminder
    # Rubric: "confirmed a previous reminder within 48hrs of booking."
    # past_appointments carries no confirmed_at timestamp, so we approximate
    # with status == "confirmed": the patient has at least once acknowledged
    # a reminder (appointment transitioned to confirmed state).
    has_prior_confirmation = any(
        a.status == "confirmed" for a in patient_history.past_appointments
    )
    if has_prior_confirmation:
        score -= 20
        factors.append("Previously confirmed a reminder (-20)")

    # Rule S2 — reliable patient: 5+ completed, zero no-shows (mutually exclusive with S4)
    if patient_history.lifetime_completed >= 5 and patient_history.lifetime_no_shows == 0:
        score -= 15
        factors.append("5+ completed appointments with zero lifetime no-shows (-15)")

    # Rule S3 — follow-up within 7 days of a prior completed visit
    if appointment.appointment_type == "follow_up":
        window_start = appointment.scheduled_at - timedelta(days=7)
        recent_completed = any(
            a.status == "completed"
            and a.completed_at is not None
            and a.completed_at >= window_start
            for a in patient_history.past_appointments
        )
        if recent_completed:
            score -= 10
            factors.append("Follow-up within 7 days of a prior completed visit (-10)")

    # Rule S4 — building loyalty: 2–4 completed, zero no-shows (mutually exclusive with S2)
    if 2 <= patient_history.lifetime_completed <= 4 and patient_history.lifetime_no_shows == 0:
        score -= 5
        factors.append("2–4 completed appointments with zero lifetime no-shows (-5)")

    # ── Clamp ─────────────────────────────────────────────────────────────────
    score = max(0, min(100, score))

    # ── Bucket ────────────────────────────────────────────────────────────────
    if score <= 25:
        bucket: Literal["low", "medium", "high"] = "low"
    elif score <= 50:
        bucket = "medium"
    else:
        bucket = "high"

    return RiskResult(
        score=score,
        bucket=bucket,
        factors=factors,
        scored_at=context.now,
    )
