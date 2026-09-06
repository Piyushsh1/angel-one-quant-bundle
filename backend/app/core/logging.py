"""Structured-ish logging setup. One place so every module logs consistently."""
from __future__ import annotations

import logging

from app.core.config import settings


def configure_logging() -> None:
    level = logging.DEBUG if settings.APP_ENV == "dev" else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Uvicorn access logs are noisy at INFO in dev; keep them at WARNING.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    # HTTP client wire logs are extremely verbose at DEBUG (every header/body of
    # every Telegram poll and broker call). Keep our own modules at DEBUG but
    # silence the transport internals.
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
