"""Structured logs. Events carry request IDs and error codes, never personal data or amounts."""

import logging
import sys

import structlog

from app.infrastructure.settings import Settings


def configure_logging(settings: Settings, process: str) -> None:
    renderer: structlog.types.Processor = (
        structlog.dev.ConsoleRenderer(colors=False)
        if settings.log_format == "console"
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[settings.log_level]
        ),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(process=process)
