"""
================================================================================
models.py  ▸  ORM tables
================================================================================
Two tables for auth: `users` and `user_sessions`. Both are multi-tenant-ready —
every future trading table will carry `user_id` and foreign-key to `users`.

Design notes:
  · Session tokens are stored HASHED, never in plaintext. A database leak must
    not hand an attacker usable sessions.
  · `email` is citext-lowered in the app layer and uniquely indexed, so two
    accounts cannot share an address.
  · Server-side sessions (a row per login) mean logout and "revoke all devices"
    are real: deleting the row instantly invalidates the cookie. A stateless JWT
    could not be revoked before expiry.
================================================================================
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Trading domain models
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    # argon2 hash string. Plaintext passwords never touch the database.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # Onboarding state, used to compute `nextStep` for the frontend.
    has_broker_connected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    has_telegram_linked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # ── Telegram notifications ────────────────────────────────────────────────
    # The user's optional phone number (collected at onboarding; not used for
    # Telegram delivery, kept for future SMS/WhatsApp channels).
    phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # The Telegram chat id we send this user's trade alerts to. Populated only
    # after the user taps Start on the bot with their one-time link code — a
    # bot cannot message a user it has never been contacted by. Unique so one
    # Telegram account maps to at most one platform user.
    telegram_chat_id: Mapped[str | None] = mapped_column(
        String(32), unique=True, nullable=True
    )
    # One-time code embedded in the bot deep link (t.me/<bot>?start=<code>).
    # Cleared once consumed. Not secret, but single-use + short-lived.
    telegram_link_code: Mapped[str | None] = mapped_column(
        String(32), unique=True, nullable=True
    )
    telegram_link_code_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # SHA-256 of the opaque token the client holds in its cookie. We compare
    # hashes, so the raw token is never persisted.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Per-session CSRF token (double-submit pattern). Not secret on its own; its
    # job is to prove the request came from our own frontend, not a cross-site
    # form.
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Idle expiry — bumped on each authenticated request.
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Absolute expiry — never extended, hard cap on session age.
    absolute_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Light audit trail; useful for a "your active devices" screen later.
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)

    user: Mapped["User"] = relationship(back_populates="sessions")


# Fast lookup of a user's live sessions (revoke-all, active-device list).
Index("idx_user_sessions_user", UserSession.user_id)
Index("idx_user_sessions_expires", UserSession.expires_at)


# ── Positions ────────────────────────────────────────────────────────────────

class Position(Base):
    """An open intraday position owned by a user."""

    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    side: Mapped[str] = mapped_column(String(4), nullable=False)      # BUY | SELL
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_price: Mapped[float] = mapped_column(Float, nullable=False)
    ltp: Mapped[float] = mapped_column(Float, nullable=False)
    pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    pnl_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship("User")


Index("idx_positions_user_open", Position.user_id, Position.is_open)


# ── Strategies ───────────────────────────────────────────────────────────────

class Strategy(Base):
    """An algorithmic trading engine registered by a user."""

    __tablename__ = "strategies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    descriptor: Mapped[str] = mapped_column(String(255), nullable=False)
    # running | stopped
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="stopped")
    trades_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship("User")


Index("idx_strategies_user", Strategy.user_id)


# ── Orders ───────────────────────────────────────────────────────────────────

class Order(Base):
    """An individual execution order."""

    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    side: Mapped[str] = mapped_column(String(4), nullable=False)       # BUY | SELL
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    # FILLED | CANCELLED | PENDING | REJECTED
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User")


Index("idx_orders_user", Order.user_id)
Index("idx_orders_user_time", Order.user_id, Order.executed_at)


# ── Watchlist ────────────────────────────────────────────────────────────────

class WatchlistItem(Base):
    """A market symbol on a user's watchlist with latest quote data."""

    __tablename__ = "watchlist_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False, default="up")
    ltp: Mapped[str] = mapped_column(String(32), nullable=False, default="0.00")
    change: Mapped[str] = mapped_column(String(32), nullable=False, default="0.00")
    change_pct: Mapped[str] = mapped_column(String(16), nullable=False, default="0.00%")
    volume: Mapped[str] = mapped_column(String(16), nullable=False, default="0")
    day_high: Mapped[str] = mapped_column(String(32), nullable=False, default="0.00")
    day_low: Mapped[str] = mapped_column(String(32), nullable=False, default="0.00")
    sparkline: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    user: Mapped["User"] = relationship("User")


Index("idx_watchlist_user", WatchlistItem.user_id)


# ── Intraday PnL curve ───────────────────────────────────────────────────────

class IntradayPnlPoint(Base):
    """One data point on the intraday P&L curve, written every minute."""

    __tablename__ = "intraday_pnl_points"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    trading_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD

    user: Mapped["User"] = relationship("User")


Index("idx_pnl_user_date", IntradayPnlPoint.user_id, IntradayPnlPoint.trading_date)


# ── Broker accounts ──────────────────────────────────────────────────────────

class BrokerAccount(Base):
    """A broker connection owned by a user.

    Credentials are NOT stored here in this cut — a real integration encrypts
    them via KMS in a separate secrets store. This row holds only the
    non-secret connection state the dashboard needs to display.
    """

    __tablename__ = "broker_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    broker_id: Mapped[str] = mapped_column(String(32), nullable=False)  # angelone…
    masked_client_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    account_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="connected"  # connected | error
    )
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User")


Index("idx_broker_accounts_user", BrokerAccount.user_id)


# ── Risk parameters ──────────────────────────────────────────────────────────

class RiskParameters(Base):
    """Per-user risk limits enforced by the trading engine.

    One row per user (the PK is the user id). Values are the guardrails the
    Risk Parameters screen edits.
    """

    __tablename__ = "risk_parameters"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Hard daily loss limit (₹, positive number). Trading halts past this.
    max_daily_loss: Mapped[float] = mapped_column(Float, nullable=False, default=5000.0)
    # Max capital deployable in one position (₹).
    max_position_size: Mapped[float] = mapped_column(
        Float, nullable=False, default=100000.0
    )
    # Max simultaneous open positions.
    max_open_positions: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5
    )
    # Default stop-loss / target as a percentage of entry.
    default_stop_loss_pct: Mapped[float] = mapped_column(
        Float, nullable=False, default=2.0
    )
    default_target_pct: Mapped[float] = mapped_column(
        Float, nullable=False, default=4.0
    )
    # Max fraction of capital used as margin (0-100).
    max_margin_utilization_pct: Mapped[float] = mapped_column(
        Float, nullable=False, default=60.0
    )

    # ── Intraday loss & drawdown ──────────────────────────────────────────
    # Max trailing profit drawdown (% from peak MTM) before locking gains.
    max_trailing_drawdown_pct: Mapped[float] = mapped_column(
        Float, nullable=False, default=25.0
    )
    # Consecutive-loss circuit breaker: halt after N losing trades in a row.
    consecutive_loss_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3
    )
    consecutive_loss_breaker_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    # ── Order execution & slippage ────────────────────────────────────────
    # Max slippage tolerance (%). Orders convert to limit at this offset.
    max_slippage_pct: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.15
    )
    # Order rate limiter — max programmatic orders per second.
    order_rate_limit_per_sec: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10
    )
    # Fat-finger caps: hard block above these per-order sizes.
    fat_finger_max_lots: Mapped[int] = mapped_column(
        Integer, nullable=False, default=200
    )
    fat_finger_max_shares: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10000
    )

    # ── Margin & overnight exposure ───────────────────────────────────────
    # Auto square-off time (HH:MM, IST) for intraday MIS positions.
    auto_square_off_time: Mapped[str] = mapped_column(
        String(5), nullable=False, default="15:15"
    )
    # Freeze carrying unhedged naked options overnight.
    overnight_options_freeze: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    # ── Alerts, webhooks & notifications ──────────────────────────────────
    sms_webhook_alerts_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    auditory_telemetry_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    # Operator's custom webhook dispatch URI (nullable — optional).
    custom_webhook_uri: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )

    # Live-trading master switch. FALSE = paper (simulated, no real broker
    # orders) — the SAFE DEFAULT for every account. Only when a user explicitly
    # turns this on does any order reach the real broker. Enforced server-side
    # at the order chokepoint, never trusted from the client.
    live_trading_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Auto-trading master switch. FALSE (default) = the background engine does
    # NOT run for this user. When TRUE, the per-user engine evaluates the
    # strategy and places trades with no per-trade approval. Independent of
    # live_trading_enabled: auto decides WHETHER the engine runs; live decides
    # whether its orders are real (else paper/simulated).
    auto_trading_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Master kill switch — when true, the engine refuses all new entries.
    kill_switch_armed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship("User")


# ── Broker connections ───────────────────────────────────────────────────────

class BrokerConnection(Base):
    """A verified link to a user's real broker (demat/trading) account.

    A row exists only after the broker's own API accepted the credentials in a
    live login. Secret fields are stored ENCRYPTED (Fernet); we never persist
    plaintext API keys, MPINs or TOTP secrets. `masked_client_id` and
    `account_name` are safe-to-display values returned by the broker.
    """

    __tablename__ = "broker_connections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # e.g. "angelone", "zerodha". One connection per broker per user.
    broker_id: Mapped[str] = mapped_column(String(32), nullable=False)

    # Encrypted credential blobs. Nullable because different brokers need
    # different fields (OAuth brokers store an access token instead of an MPIN).
    enc_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    enc_api_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    enc_mpin: Mapped[str | None] = mapped_column(Text, nullable=True)
    enc_totp_secret: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Encrypted live session tokens from the broker login. These let us make
    # authenticated calls (RMS, positions, orderbook, quotes) AFTER connect
    # without re-login. Angel One JWTs are short-lived; the refresh token mints
    # a new JWT. Stored encrypted at rest like every other broker secret.
    enc_jwt_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    enc_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    enc_feed_token: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Safe-to-display identity returned by the broker on a successful login.
    client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    masked_client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    account_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Available cash/margin read from the broker's RMS at the last verification.
    # Point-in-time (margin moves constantly) but real — never invented.
    broker_available_cash: Mapped[float | None] = mapped_column(Float, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship("User")


# One connection per (user, broker); reconnecting updates the existing row.
Index(
    "idx_broker_conn_user_broker",
    BrokerConnection.user_id,
    BrokerConnection.broker_id,
    unique=True,
)
