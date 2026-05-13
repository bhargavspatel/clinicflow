"""Structlog JSON logging configuration.

Call configure_logging() exactly once at process startup — in main.py for the
API process and in WorkerSettings.on_startup for the Arq worker process.

Every log line is a JSON object with these guaranteed fields:
  timestamp   ISO-8601 UTC
  level       debug / info / warning / error / critical
  logger      dotted module name
  event       the log message / event name
  request_id  per-HTTP-request UUID (empty string in worker context)
  tenant_id   from JWT claim (empty string until deps.py binds it)
  user_id     from JWT claim (empty string until deps.py binds it)

Additional keyword arguments passed at the call site become extra JSON fields.
"""
import logging
import sys
from typing import Any

import structlog


def configure_logging(log_level: int = logging.INFO) -> None:
    # Processors applied to both structlog-native and foreign (stdlib) records.
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # Suppress noisy libraries that produce a log line per query/request.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
