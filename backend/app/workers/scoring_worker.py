"""Nightly risk-scoring worker.

Runs as an Arq cron job at 02:00 UTC every day.
Scores all active appointments (scheduled / confirmed / rescheduled) that fall
within the next 7 days across every tenant.

The worker DB engine is created in WorkerSettings.on_startup and passed via ctx.
Each appointment is scored in its own session so one failure cannot roll back
the entire batch.
"""
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.appointment import Appointment
from app.services.appointment_service import score_appointment

logger = structlog.get_logger(__name__)

_ACTIVE_STATUSES = ("scheduled", "confirmed", "rescheduled")
_LOOKAHEAD_DAYS = 7


async def score_upcoming_appointments(ctx: dict) -> dict:
    """Score every active appointment scheduled in the next 7 days, all tenants.

    Returns a summary dict so Arq can record it in the job result:
        {"scored": int, "failed": int, "total": int, "duration_ms": int}
    """
    started_at = datetime.now(timezone.utc)
    factory: async_sessionmaker[AsyncSession] = ctx["db_factory"]

    # ── 1. Collect appointment IDs to score ───────────────────────────────────
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(days=_LOOKAHEAD_DAYS)

    async with factory() as db:
        rows = await db.execute(
            select(Appointment.id, Appointment.tenant_id).where(
                Appointment.scheduled_at >= now,
                Appointment.scheduled_at <= window_end,
                Appointment.status.in_(_ACTIVE_STATUSES),
            )
        )
        # Collect plain tuples immediately so the session can close cleanly
        to_score: list[tuple[uuid.UUID, uuid.UUID]] = list(rows.all())

    total = len(to_score)
    logger.info("scoring_start", total=total, lookahead_days=_LOOKAHEAD_DAYS)

    # ── 2. Score each appointment in its own session ───────────────────────────
    scored = 0
    failed = 0

    for appt_id, tenant_id in to_score:
        try:
            async with factory() as db:
                await score_appointment(db, tenant_id, appt_id)
            scored += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.error(
                "scoring_failed",
                appointment_id=str(appt_id),
                tenant_id=str(tenant_id),
                error=repr(exc),
            )

    # ── 3. Log summary ────────────────────────────────────────────────────────
    duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
    logger.info(
        "scoring_done",
        scored=scored,
        failed=failed,
        total=total,
        duration_ms=duration_ms,
    )

    return {"scored": scored, "failed": failed, "total": total, "duration_ms": duration_ms}
