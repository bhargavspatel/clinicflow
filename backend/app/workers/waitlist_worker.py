"""Waitlist fill worker.

fill_waitlist_task is enqueued by the appointments route whenever an appointment
transitions to 'cancelled'.  It finds the best-matching waitlist entry (if any)
and notifies the patient via SMS.

Idempotency: if a 'waitlist_filled' event already exists for the appointment
(e.g. the task was retried after a transient failure that occurred after the
commit), the task exits without a second notification.
"""
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.models.appointment import Appointment
from app.models.appointment_event import AppointmentEvent
from app.services import waitlist_service

logger = structlog.get_logger(__name__)


async def fill_waitlist_task(ctx: dict, appointment_id: uuid.UUID) -> dict:
    """Find and notify a waitlist patient for a cancelled appointment slot.

    Returns a summary dict persisted by Arq as the job result.
    """
    factory: async_sessionmaker[AsyncSession] = ctx["db_factory"]
    twilio_client                              = ctx["twilio_client"]
    settings                                   = get_settings()

    async with factory() as db:
        appt = await db.get(Appointment, appointment_id)
        if appt is None:
            logger.warning("waitlist_appointment_not_found", appointment_id=str(appointment_id))
            return {"status": "appointment_not_found"}

        if appt.status != "cancelled":
            logger.info(
                "waitlist_skip",
                appointment_id=str(appointment_id),
                status=appt.status,
            )
            return {"status": "not_cancelled", "appointment_status": appt.status}

        # Idempotency: skip if already filled on a previous run
        already_filled = (
            await db.execute(
                select(AppointmentEvent).where(
                    AppointmentEvent.appointment_id == appointment_id,
                    AppointmentEvent.event_type == "waitlist_filled",
                )
            )
        ).scalar_one_or_none()
        if already_filled is not None:
            logger.info("waitlist_already_filled", appointment_id=str(appointment_id))
            return {"status": "already_filled"}

        entry = await waitlist_service.find_waitlist_match(db, appt)
        if entry is None:
            logger.info("waitlist_no_match", appointment_id=str(appointment_id))
            return {"status": "no_match"}

        notif = await waitlist_service.notify_waitlist_patient(
            db=db,
            entry=entry,
            appointment=appt,
            twilio_client=twilio_client,
            from_number=settings.twilio_from_number,
        )

    logger.info(
        "waitlist_notified",
        notification_id=str(notif.id),
        appointment_id=str(appointment_id),
        waitlist_entry_id=str(entry.id),
    )
    return {
        "status":             "notified",
        "waitlist_entry_id":  str(entry.id),
        "notification_id":    str(notif.id),
    }
