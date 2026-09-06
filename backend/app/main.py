"""
================================================================================
main.py  ▸  FastAPI application entry point
================================================================================
Wires the app: settings validation, logging, CORS, rate limiting, schema
bootstrap, and the v1 router.

CONCURRENCY / MULTI-USER MODEL
──────────────────────────────
The app is fully async. FastAPI serves each request as a coroutine on one event
loop; each gets its own database session from the pool and its own request
scope. Two users acting at the same instant share nothing mutable:

  · no global "current user" — the subject is resolved per request from that
    request's own session cookie;
  · one AsyncSession per request, committed/rolled back within that request;
  · sessions are rows in Postgres, so a login on device A cannot disturb a
    login on device B or another user's session.

This is what lets many users interact with the frontend concurrently with no
cross-talk.
================================================================================
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.infrastructure.database.bootstrap import create_schema
from app.infrastructure.database.session import engine
from app.infrastructure.telegram import worker as telegram_worker
from app.services.strategy_engine import supervisor as engine_supervisor

log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    settings.validate_for_prod()
    await create_schema()
    # One shared Telegram bot serves all users; this background task completes
    # each user's link handshake and delivers their per-user alerts.
    telegram_task = await telegram_worker.start_worker()
    # Per-user auto-trading engine supervisor. Idles unless a user has turned
    # auto-trading on; each active user is traded on their own broker session,
    # paper unless they enabled live.
    engine_task = await engine_supervisor.start()
    log.info("Barbell auth API started | env=%s", settings.APP_ENV)
    yield
    await engine_supervisor.stop(engine_task)
    await telegram_worker.stop_worker(telegram_task)
    await engine.dispose()
    log.info("Barbell auth API stopped")


app = FastAPI(
    title="Barbell Auth API",
    version="1.0.0",
    # Hide interactive docs in production — they enumerate the API surface.
    docs_url=None if settings.is_prod else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_prod else "/openapi.json",
    lifespan=lifespan,
)

# CORS: credentialed requests from the allow-listed frontend only. The
# X-CSRF-Token header must be permitted so the SPA can send it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)

if settings.is_prod:
    # Reject requests with a spoofed Host header in production.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])

app.include_router(api_router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.APP_ENV}
