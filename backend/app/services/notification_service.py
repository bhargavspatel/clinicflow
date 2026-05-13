"""Notification service — zero Twilio, OpenAI, or FastAPI imports.

enqueue_reminder     Create a queued Notification row + 'reminded' audit event.
                     The worker fills `body`, calls OpenAI for content, and
                     dispatches via Twilio.  sequence_number is stored in the
                     event_metadata so the worker can check idempotency.

mark_delivered       Record Twilio delivery confirmation on an existing row.
                     Called from the Twilio status-callback webhook handler.

handle_inbound_reply Parse an inbound SMS reply, transition the nearest upcoming
                     appointment to 'confirmed' on a YES, and persist the inbound
                     message.  Reschedule intent is recorded but does not mutate
                     the appointment — the patient must follow the magic link.
"""
import re
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.appointment_event import AppointmentEvent
from app.models.notification import Notification
from app.models.patient import Patient


# ── Domain exceptions ─────────────────────────────────────────────────────────

class NotFoundError(Exception):
    pass


class NoActiveAppointmentError(Exception):
    pass


# ── Reply intent parsing ──────────────────────────────────────────────────────

ReplyIntent = Literal["confirmed", "reschedule_requested", "unrecognized"]

# Matches: YES, yes, Y, yep, yeah, confirm, confirmed, 1
_YES_RE = re.compile(
    r"^\s*(?:y(?:es|ep|eah)?|confirm(?:ed)?|1)\s*$",
    re.IGNORECASE,
)

# Matches reschedule/cancel/no/nope/can't anywhere in the message.
# NOTE: Twilio intercepts STOP/HELP/START before our webhook — no need to handle them.
_RESCHEDULE_RE = re.compile(
    r"\b(?:reschedule|cancel(?:led)?|no|nope|can'?t)\b",
    re.IGNORECASE,
)

# Appointment statuses that a YES reply can transition to 'confirmed'.
_CONFIRMABLE_STATUSES = frozenset({"scheduled", "rescheduled"})


def _parse_intent(body: str) -> ReplyIntent:
    if _YES_RE.match(body):
        return "confirmed"
    if _RESCHEDULE_RE.search(body):
        return "reschedule_requested"
    return "unrecognized"


# ── Output type ───────────────────────────────────────────────────────────────

class InboundReplyResult(BaseModel):
    intent: ReplyIntent
    appointment_id: uuid.UUID
    notification_id: uuid.UUID
    tenant_id: uuid.UUID


# ── Service functions ─────────────────────────────────────────────────────────

async def enqueue_reminder(
    db: AsyncSession,
    appointment_id: uuid.UUID,
    sequence_number: int,
) -> Notification:
    """Create a queued Notification row and append a 'reminded' audit event.

    Called by the nightly notification worker for each appointment that needs
    a reminder.  No tenant_id parameter — the worker operates across all tenants
    and retrieves tenant context from the appointment row itself.

    The returned Notification has body="" and status="queued".  The worker is
    responsible for generating the message body via OpenAI and updating the row
    before dispatching via Twilio.
    """
    result = await db.execute(
        select(Appointment).where(Appointment.id == appointment_id)
    )
    appt = result.scalar_one_or_none()
    if appt is None:
        raise NotFoundError(f"Appointment {appointment_id} not found")

    notif = Notification(
        tenant_id=appt.tenant_id,
        appointment_id=appt.id,
        channel="sms",
        direction="outbound",
        body="",        # worker fills this after LLM generation
        status="queued",
    )
    db.add(notif)
    await db.flush()   # populate notif.id before the event references it

    db.add(AppointmentEvent(
        tenant_id=appt.tenant_id,
        appointment_id=appt.id,
        event_type="reminded",
        actor_id=None,  # system-generated
        event_metadata={
            "sequence_number": sequence_number,
            "notification_id": str(notif.id),
        },
    ))

    await db.commit()
    await db.refresh(notif)
    return notif


async def mark_delivered(
    db: AsyncSession,
    notification_id: uuid.UUID,
    twilio_sid: str,
) -> Notification:
    """Record Twilio delivery confirmation on an existing Notification row.

    Called from the Twilio status-callback webhook endpoint after the worker
    has already set status='sent' and recorded sent_at.
    """
    result = await db.execute(
        select(Notification).where(Notification.id == notification_id)
    )
    notif = result.scalar_one_or_none()
    if notif is None:
        raise NotFoundError(f"Notification {notification_id} not found")

    notif.twilio_sid = twilio_sid
    notif.status = "delivered"
    notif.delivered_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(notif)
    return notif


async def handle_inbound_reply(
    db: AsyncSession,
    from_number: str,
    body: str,
) -> InboundReplyResult:
    """Parse an inbound SMS reply, act on confirmed/reschedule intent.

    Performs a cross-tenant phone lookup because inbound Twilio webhooks carry
    no tenant context — the caller's phone number is the only identifier.

    If multiple patients share a phone number across tenants, the one with the
    nearest upcoming active appointment is selected.

    On YES intent: transitions the appointment to 'confirmed' (if currently
    'scheduled' or 'rescheduled') and appends a 'confirmed' audit event.

    On reschedule_requested: records the intent in the inbound notification row
    but does NOT mutate the appointment — a status change requires a new
    scheduled_at, which the patient provides via the magic-link reschedule flow.

    On unrecognized intent: persists the message for audit purposes only.

    Always persists one inbound Notification row regardless of intent.

    Raises:
        NoActiveAppointmentError: phone number unknown or no upcoming active
            appointment found for any matching patient.
    """
    # ── Find patient(s) by phone — may span tenants ───────────────────────────
    patient_rows = await db.execute(
        select(Patient).where(Patient.phone == from_number)
    )
    patients = list(patient_rows.scalars().all())
    if not patients:
        raise NoActiveAppointmentError(
            f"No patient found with phone {from_number}"
        )

    # Among all matching patients find the nearest upcoming active appointment.
    now = datetime.now(timezone.utc)
    best_appt: Optional[Appointment] = None

    for patient in patients:
        appt_rows = await db.execute(
            select(Appointment)
            .where(
                Appointment.patient_id == patient.id,
                Appointment.scheduled_at >= now,
                Appointment.status.in_(("scheduled", "confirmed", "rescheduled")),
            )
            .order_by(Appointment.scheduled_at)
            .limit(1)
        )
        appt = appt_rows.scalar_one_or_none()
        if appt is None:
            continue
        if best_appt is None or appt.scheduled_at < best_appt.scheduled_at:
            best_appt = appt

    if best_appt is None:
        raise NoActiveAppointmentError(
            f"No upcoming appointment found for {from_number}"
        )

    intent = _parse_intent(body)

    # ── Transition appointment on YES ─────────────────────────────────────────
    if intent == "confirmed" and best_appt.status in _CONFIRMABLE_STATUSES:
        best_appt.status = "confirmed"
        db.add(AppointmentEvent(
            tenant_id=best_appt.tenant_id,
            appointment_id=best_appt.id,
            event_type="confirmed",
            actor_id=None,  # patient replied via SMS — no users.id
            event_metadata={"source": "sms_reply", "from_number": from_number},
        ))

    # ── Persist inbound notification (audit trail for every reply) ────────────
    notif = Notification(
        tenant_id=best_appt.tenant_id,
        appointment_id=best_appt.id,
        channel="sms",
        direction="inbound",
        body=body,
        status="received",
    )
    db.add(notif)
    await db.flush()

    await db.commit()
    await db.refresh(notif)
    return InboundReplyResult(
        intent=intent,
        appointment_id=best_appt.id,
        notification_id=notif.id,
        tenant_id=best_appt.tenant_id,
    )
