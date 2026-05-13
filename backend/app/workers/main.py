"""Arq worker entry point.

Start with:
    python -m arq app.workers.main.WorkerSettings

The worker creates its own SQLAlchemy engine (separate pool from the API process).
DB engine, session factory, OpenAI client, and Twilio client are stored in the
Arq ctx dict so every task can reuse them without reconnecting on each run.
"""
import sentry_sdk
import structlog
from arq import cron
from arq.connections import RedisSettings
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from twilio.rest import Client as TwilioClient

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.workers.notification_worker import send_sms_task
from app.workers.scheduling_worker import schedule_reminders
from app.workers.scoring_worker import score_upcoming_appointments
from app.workers.waitlist_worker import fill_waitlist_task

configure_logging()
logger = structlog.get_logger(__name__)


async def startup(ctx: dict) -> None:
    settings = get_settings()

    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            integrations=[],
            traces_sample_rate=0.0,
            send_default_pii=False,
        )

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    ctx["engine"]     = engine
    ctx["db_factory"] = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    ctx["openai_client"] = AsyncOpenAI(api_key=settings.openai_api_key)
    ctx["twilio_client"] = TwilioClient(
        settings.twilio_account_sid, settings.twilio_auth_token
    )

    logger.info(
        "worker_startup",
        db=settings.database_url.split("@")[-1],
        openai_model=settings.openai_sms_model,
    )


async def shutdown(ctx: dict) -> None:
    await ctx["engine"].dispose()
    await ctx["openai_client"].close()
    logger.info("worker_shutdown")


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


class WorkerSettings:
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = _redis_settings()

    functions = [
        score_upcoming_appointments,
        schedule_reminders,     # hourly — finds reminders due in the next hour
        send_sms_task,          # enqueued per-notification by schedule_reminders
        fill_waitlist_task,     # enqueued on appointment cancellation
    ]

    cron_jobs = [
        # Nightly at 02:00 UTC — after midnight DB maintenance, before clinic opens
        cron(score_upcoming_appointments, hour=2, minute=0),
        # Every hour — enqueues send_sms_task for reminders due in the next 60 min
        cron(schedule_reminders, minute=0),
    ]
