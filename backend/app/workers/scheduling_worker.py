"""Reminder scheduling worker.

Runs as an Arq cron job every hour.  For each active, scored appointment
whose next reminder trigger time falls within the coming hour, it:
  1. Checks whether that sequence was already enqueued (idempotency).
  2. Creates a queued Notification row via enqueue_reminder().
  3. Dispatches send_sms_task to Arq so the message is sent immediately.

Reminder schedule by risk bucket
  low    → 1 reminder  : 24 hrs before
  medium → 2 reminders : 48 hrs, 24 hrs before
  high   → 3 reminders : 72 hrs, 48 hrs, 24 hrs before

Appointments without a risk_bucket (not yet scored) are skipped — the nightly
scoring cron (02:00 UTC) runs before the first reminder window opens.

Idempotency: an appointment_events row with event_type='reminded' and
event_metadata.sequence_number == N is treated as proof that sequence N was
already enqueued.  Even if the Arq job was lost after the DB commit, the
send_sms_task idempotency guard (checks notification.status) prevents
double-sends on any manual re-enqueue.
"""
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.appointment import Appointment
from app.models.appointment_event import AppointmentEvent
from app.services.notification_service import enqueue_reminder

logger = structlog.get_logger(__name__)

# ── Reminder schedule: bucket → [hours_before_appointment, ...] ──────────────
# Sequences are numbered 1-based in the order listed here.
_REMINDER_SCHEDULE: dict[str, list[int]] = {
    "low":    [24],
    "medium": [48, 24],
    "high":   [72, 48, 24],
}

_ACTIVE_STATUSES = ("scheduled", "confirmed", "rescheduled")

# Max trigger offset across all buckets + 1 cron-window hour.
# No appointment beyond this can have a trigger due in the next hour.
_MAX_LOOKAHEAD_HOURS = 73   # 72hr (high seq-1) + 1hr window

# Minimum appointment distance: no appointment closer than this can have
# an unsent trigger due in the next hour.
_MIN_LOOKAHEAD_HOURS = 24   # 24hr trigger is the nearest possible


async def schedule_reminders(ctx: dict) -> dict:
    """Enqueue send_sms_task for every reminder due in the next hour.

    Returns a summary dict persisted by Arq:
        {"scheduled": int, "skipped": int, "duration_ms": int}
    """
    started_at = datetime.now(timezone.utc)
    factory: async_sessionmaker[AsyncSession] = ctx["db_factory"]
    redis                                     = ctx["redis"]

    now        = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=1)

    # ── 1. Fetch candidate appointments ───────────────────────────────────────
    # Narrow the DB scan to appointments whose scheduled_at falls within the
    # range where any trigger time could land in [now, now+1hr).
    async with factory() as db:
        rows = await db.execute(
            select(Appointment).where(
                Appointment.scheduled_at >= now + timedelta(hours=_MIN_LOOKAHEAD_HOURS),
                Appointment.scheduled_at <  now + timedelta(hours=_MAX_LOOKAHEAD_HOURS),
                Appointment.status.in_(_ACTIVE_STATUSES),
                Appointment.risk_bucket.isnot(None),
            )
        )
        appointments = list(rows.scalars().all())

    logger.info(
        "schedule_reminders_candidates",
        count=len(appointments),
        lookahead_hours=_MAX_LOOKAHEAD_HOURS,
    )

    scheduled = 0
    skipped   = 0

    # ── 2. For each appointment, check each sequence ──────────────────────────
    for appt in appointments:
        trigger_hours: list[int] = _REMINDER_SCHEDULE.get(appt.risk_bucket, [])

        for seq_num, hours_before in enumerate(trigger_hours, start=1):
            trigger_time = appt.scheduled_at - timedelta(hours=hours_before)

            if not (now <= trigger_time < window_end):
                continue  # this sequence is not due in the current cron window

            # ── 3. Idempotency check + enqueue in one session ─────────────────
            notif_id: uuid.UUID | None = None
            async with factory() as db:
                existing = await db.execute(
                    select(AppointmentEvent).where(
                        AppointmentEvent.appointment_id == appt.id,
                        AppointmentEvent.event_type    == "reminded",
                        AppointmentEvent.event_metadata["sequence_number"].astext
                        == str(seq_num),
                    )
                )
                if existing.scalar_one_or_none() is not None:
                    skipped += 1
                    logger.info(
                        "schedule_reminders_skip",
                        appointment_id=str(appt.id),
                        risk_bucket=appt.risk_bucket,
                        seq=seq_num,
                    )
                    continue

                # Creates Notification row + 'reminded' event, commits, returns notif
                notif = await enqueue_reminder(db, appt.id, seq_num)
                notif_id = notif.id

            # ── 4. Dispatch Arq job outside the DB session ────────────────────
            # ctx["redis"] is ArqRedis — has enqueue_job for dispatching tasks.
            await redis.enqueue_job("send_sms_task", notif_id)

            scheduled += 1
            logger.info(
                "schedule_reminders_enqueued",
                appointment_id=str(appt.id),
                risk_bucket=appt.risk_bucket,
                seq=seq_num,
                trigger=trigger_time.isoformat(),
                notification_id=str(notif_id),
            )

    # ── 5. Summary ─────────────────────────────────────────────────────────────
    duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
    logger.info(
        "schedule_reminders_done",
        scheduled=scheduled,
        skipped=skipped,
        duration_ms=duration_ms,
    )
    return {"scheduled": scheduled, "skipped": skipped, "duration_ms": duration_ms}
