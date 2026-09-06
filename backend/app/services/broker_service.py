"""
================================================================================
services/broker_service.py  ▸  Broker connection business logic
================================================================================
Connects a user's real broker account by VERIFYING their credentials live
against the broker, then persisting them encrypted. There is no "assume valid"
path: `hasBrokerConnected` is set only after the broker's own API accepted a
real login.

Currently implements Angel One (SmartAPI). Other brokers listed in the frontend
registry are not yet wired here and are rejected with a clear message rather
than silently accepted.
================================================================================
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.infrastructure.brokers import angelone
from app.infrastructure.brokers.angelone import BrokerAuthError
from app.infrastructure.database.models import BrokerConnection, User

log = logging.getLogger("broker")

# Brokers with a real, tested verification path in this service.
_SUPPORTED = {"angelone"}

# Required credential fields per broker (must match frontend config/brokers.ts).
_REQUIRED_FIELDS = {
    "angelone": ("apiKey", "clientId", "mpin", "totpSecret"),
}


class BrokerError(Exception):
    """Domain-level broker failure. `code` maps to an HTTP status in the router."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _mask_client_id(client_id: str) -> str:
    """Show only the ends of an identifier, e.g. 'A12345678' → 'A12***78'."""
    s = client_id.strip()
    if len(s) <= 4:
        return "*" * len(s)
    return f"{s[:3]}***{s[-2:]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def connect_broker(
    db: AsyncSession,
    *,
    user: User,
    broker_id: str,
    credentials: dict[str, str],
) -> BrokerConnection:
    """Verify credentials with the broker in real time, then persist them.

    Raises BrokerError on unsupported broker, missing fields, or a live
    verification failure.
    """
    broker_id = (broker_id or "").strip().lower()
    if broker_id not in _SUPPORTED:
        raise BrokerError(
            "unsupported",
            "This broker is not available for automated connection yet.",
        )

    # Validate presence of every required field before hitting the broker.
    required = _REQUIRED_FIELDS[broker_id]
    missing = [f for f in required if not (credentials.get(f) or "").strip()]
    if missing:
        raise BrokerError("invalid", f"Missing required fields: {', '.join(missing)}")

    if broker_id == "angelone":
        return await _connect_angelone(db, user=user, credentials=credentials)

    # Unreachable given the guard above, kept for exhaustiveness.
    raise BrokerError("unsupported", "Unsupported broker.")


async def _connect_angelone(
    db: AsyncSession, *, user: User, credentials: dict[str, str]
) -> BrokerConnection:
    api_key = credentials["apiKey"].strip()
    client_id = credentials["clientId"].strip()
    mpin = credentials["mpin"].strip()
    totp_secret = credentials["totpSecret"].strip()

    # ── Real-time verification against Angel One ──────────────────────────────
    try:
        verified = await angelone.verify_credentials(
            api_key=api_key,
            client_id=client_id,
            mpin=mpin,
            totp_secret=totp_secret,
        )
    except BrokerAuthError as err:
        # Malformed input (422) vs transient broker/network problem (503) vs a
        # definite credential rejection by the broker (401).
        if err.invalid_input:
            code = "invalid"
        elif err.retryable:
            code = "unavailable"
        else:
            code = "unauthorized"
        raise BrokerError(code, err.message) from err

    # ── Persist, encrypting every secret at rest ──────────────────────────────
    existing = (
        await db.execute(
            select(BrokerConnection).where(
                BrokerConnection.user_id == user.id,
                BrokerConnection.broker_id == "angelone",
            )
        )
    ).scalar_one_or_none()

    conn = existing or BrokerConnection(
        id=uuid.uuid4(), user_id=user.id, broker_id="angelone"
    )
    conn.enc_api_key = security.encrypt_secret(api_key)
    conn.enc_mpin = security.encrypt_secret(mpin)
    conn.enc_totp_secret = security.encrypt_secret(totp_secret)
    conn.enc_api_secret = None
    # Persist the live session tokens (encrypted) so the dashboard can make
    # authenticated calls after connect without another login.
    conn.enc_jwt_token = (
        security.encrypt_secret(verified.jwt_token) if verified.jwt_token else None
    )
    conn.enc_refresh_token = (
        security.encrypt_secret(verified.refresh_token)
        if verified.refresh_token
        else None
    )
    conn.enc_feed_token = (
        security.encrypt_secret(verified.feed_token) if verified.feed_token else None
    )
    conn.client_id = verified.client_id
    conn.masked_client_id = _mask_client_id(verified.client_id)
    conn.account_name = verified.account_name
    conn.broker_available_cash = verified.available_margin
    conn.is_active = True
    conn.last_verified_at = _now()

    if existing is None:
        db.add(conn)

    # Advance onboarding — this is the ONLY place the flag is set true, and only
    # after a real broker login succeeded.
    user.has_broker_connected = True

    log.info(
        "broker connected user=%s broker=angelone client=%s",
        user.email,
        conn.masked_client_id,
    )
    # Stash the just-fetched live margin so the router can echo it back without
    # a second broker round-trip. Not persisted; it changes constantly.
    conn._live_available_margin = verified.available_margin  # type: ignore[attr-defined]
    return conn


async def get_connection(
    db: AsyncSession, *, user_id: uuid.UUID, broker_id: str = "angelone"
) -> BrokerConnection | None:
    return (
        await db.execute(
            select(BrokerConnection).where(
                BrokerConnection.user_id == user_id,
                BrokerConnection.broker_id == broker_id,
            )
        )
    ).scalar_one_or_none()


async def active_connection(
    db: AsyncSession, *, user_id: uuid.UUID
) -> BrokerConnection | None:
    """The user's active broker connection, if any (first active one)."""
    return (
        await db.execute(
            select(BrokerConnection)
            .where(
                BrokerConnection.user_id == user_id,
                BrokerConnection.is_active.is_(True),
            )
            .order_by(BrokerConnection.last_verified_at.desc())
        )
    ).scalars().first()


async def list_connections(
    db: AsyncSession, *, user_id: uuid.UUID
) -> list[BrokerConnection]:
    """Every broker connection the caller has, newest first."""
    return list(
        (
            await db.execute(
                select(BrokerConnection)
                .where(BrokerConnection.user_id == user_id)
                .order_by(BrokerConnection.last_verified_at.desc())
            )
        ).scalars().all()
    )


async def disconnect(
    db: AsyncSession, *, user_id: uuid.UUID, connection_id: str
) -> bool:
    """Delete one of the caller's broker connections. Returns True if removed.

    Also clears the onboarding flag when the last connection is gone, so the UI
    reflects that no broker is linked anymore.
    """
    try:
        conn_uuid = uuid.UUID(connection_id)
    except ValueError:
        return False

    conn = (
        await db.execute(
            select(BrokerConnection).where(
                BrokerConnection.id == conn_uuid,
                BrokerConnection.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if conn is None:
        return False

    await db.delete(conn)
    await db.flush()

    remaining = await list_connections(db, user_id=user_id)
    if not remaining:
        from app.infrastructure.database.models import User

        user = (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if user is not None:
            user.has_broker_connected = False
    return True
