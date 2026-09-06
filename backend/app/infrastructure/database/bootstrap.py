"""
Schema bootstrap.

Creates tables from the ORM metadata if they do not exist and applies additive
columns to existing tables. No demo/seed data is inserted — every account
starts empty and is populated only by real, live broker data.

A real migration tool (Alembic) should own schema evolution before this ships
to prod — create_all cannot ALTER an existing table.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from app.infrastructure.database.models import Base
from app.infrastructure.database.session import engine

log = logging.getLogger("bootstrap")


# Columns added to existing tables after their first release. create_all only
# CREATEs missing tables — it never ALTERs an existing one — so additive columns
# are applied here idempotently (IF NOT EXISTS) until Alembic owns migrations.
# Each entry: (table, column, DDL type + constraints).
_ADDITIVE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("users", "phone_number", "VARCHAR(20)"),
    ("users", "telegram_chat_id", "VARCHAR(32)"),
    ("users", "telegram_link_code", "VARCHAR(32)"),
    ("users", "telegram_link_code_expires_at", "TIMESTAMPTZ"),
    ("broker_connections", "enc_jwt_token", "TEXT"),
    ("broker_connections", "enc_refresh_token", "TEXT"),
    ("broker_connections", "enc_feed_token", "TEXT"),
    # RMS guardrails added after the first risk_parameters release. Each carries
    # a DEFAULT so existing rows get the conservative desk value immediately.
    ("risk_parameters", "max_trailing_drawdown_pct", "DOUBLE PRECISION NOT NULL DEFAULT 25.0"),
    ("risk_parameters", "consecutive_loss_limit", "INTEGER NOT NULL DEFAULT 3"),
    ("risk_parameters", "consecutive_loss_breaker_enabled", "BOOLEAN NOT NULL DEFAULT TRUE"),
    ("risk_parameters", "max_slippage_pct", "DOUBLE PRECISION NOT NULL DEFAULT 0.15"),
    ("risk_parameters", "order_rate_limit_per_sec", "INTEGER NOT NULL DEFAULT 10"),
    ("risk_parameters", "fat_finger_max_lots", "INTEGER NOT NULL DEFAULT 200"),
    ("risk_parameters", "fat_finger_max_shares", "INTEGER NOT NULL DEFAULT 10000"),
    ("risk_parameters", "auto_square_off_time", "VARCHAR(5) NOT NULL DEFAULT '15:15'"),
    ("risk_parameters", "overnight_options_freeze", "BOOLEAN NOT NULL DEFAULT TRUE"),
    ("risk_parameters", "sms_webhook_alerts_enabled", "BOOLEAN NOT NULL DEFAULT TRUE"),
    ("risk_parameters", "auditory_telemetry_enabled", "BOOLEAN NOT NULL DEFAULT TRUE"),
    ("risk_parameters", "custom_webhook_uri", "VARCHAR(500)"),
    # Live-trading switch — defaults FALSE so every existing account stays on
    # paper until the user explicitly opts in.
    ("risk_parameters", "live_trading_enabled", "BOOLEAN NOT NULL DEFAULT FALSE"),
    # Auto-trading switch — defaults FALSE so the engine never runs for an
    # account until the user turns it on.
    ("risk_parameters", "auto_trading_enabled", "BOOLEAN NOT NULL DEFAULT FALSE"),
)


async def _apply_additive_columns() -> None:
    """Add new nullable columns to existing tables without dropping data."""
    async with engine.begin() as conn:
        for table, column, ddl_type in _ADDITIVE_COLUMNS:
            await conn.execute(
                text(
                    f'ALTER TABLE "{table}" '
                    f'ADD COLUMN IF NOT EXISTS "{column}" {ddl_type}'
                )
            )


async def create_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _apply_additive_columns()
    log.info("Schema verified (all tables) — no demo data seeded")
