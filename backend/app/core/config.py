"""
================================================================================
config.py  ▸  Application settings
================================================================================
All configuration is read from the environment and validated once at startup,
so a missing or malformed value fails loudly here rather than surfacing as a
runtime error mid-request.

Security-sensitive defaults are deliberately SAFE-BY-DEFAULT: cookies are
Secure and httpOnly, CORS is closed unless an origin is allow-listed, and the
session secret has no default in production.
================================================================================
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Environment ────────────────────────────────────────────────────────
    APP_ENV: Literal["dev", "preprod", "prod"] = "dev"

    # ── Database ─────────────────────────────────────────────────────────────
    # Async DSN. The +psycopg suffix selects psycopg 3's async driver.
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "algo_barbell_dev"
    DB_USER: str = "algo"
    DB_PASSWORD: str = "algo"
    DATABASE_URL: str = ""

    # ── Sessions & cookies ───────────────────────────────────────────────────
    # Signs nothing directly (sessions are opaque + DB-backed), but reserved for
    # future signed artifacts. In prod it MUST be set to a long random value.
    SESSION_SECRET: str = "dev-only-insecure-secret-change-me"
    SESSION_COOKIE_NAME: str = "barbell_session"
    CSRF_COOKIE_NAME: str = "barbell_csrf"
    # Idle + absolute session lifetimes.
    SESSION_IDLE_MINUTES: int = 60 * 12          # 12h of inactivity → expire
    SESSION_ABSOLUTE_HOURS: int = 24 * 7         # hard cap regardless of use
    #: "remember this device" extends the idle window to this.
    SESSION_REMEMBER_DAYS: int = 30

    # ── CORS ───────────────────────────────────────────────────────────────
    # Comma-separated allow-list. The frontend origin(s) only. Never "*", because
    # this API uses cookie credentials and "*" + credentials is both invalid and
    # unsafe.
    CORS_ORIGINS: str = "http://localhost:3000"

    # ── Rate limits ──────────────────────────────────────────────────────────
    LOGIN_RATE_LIMIT: str = "10/minute"
    SIGNUP_RATE_LIMIT: str = "5/minute"
    # Broker connect performs a live third-party login; keep it tight to limit
    # credential-stuffing against the broker through us.
    BROKER_CONNECT_RATE_LIMIT: str = "5/minute"

    # ── Broker integration (Angel One SmartAPI) ──────────────────────────────
    # Production REST host. Overridable so tests/staging can point elsewhere.
    ANGELONE_API_BASE_URL: str = "https://apiconnect.angelone.in"
    # Seconds to wait on each live broker HTTP call before giving up.
    ANGELONE_HTTP_TIMEOUT: float = 12.0

    # ── Telegram notifications ───────────────────────────────────────────────
    # One shared bot serves ALL users. The token identifies the bot (from
    # @BotFather), never a person. Each user is reached by their own chat_id,
    # captured when they tap Start on the bot with a one-time link code.
    # Empty token → notifications are disabled (link endpoint returns 503).
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_BOT_USERNAME: str = ""          # e.g. "Barbell_1_bot" (no @)
    # How often the background worker polls Telegram getUpdates, in seconds.
    TELEGRAM_POLL_INTERVAL: float = 2.0
    # A link code is only valid for this many minutes after it is issued.
    TELEGRAM_LINK_CODE_TTL_MINUTES: int = 30

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.TELEGRAM_BOT_TOKEN.strip())

    # ── Credential encryption at rest ────────────────────────────────────────
    # Fernet key (urlsafe base64, 32 bytes) used to encrypt broker credentials
    # before they touch the database. MUST be set to a real generated key in
    # any non-dev environment. If empty, a key is derived from SESSION_SECRET so
    # local dev works out of the box — never rely on that in prod.
    CREDENTIAL_ENCRYPTION_KEY: str = ""

    @field_validator("DATABASE_URL", mode="after")
    @classmethod
    def _assemble_dsn(cls, v: str, info) -> str:  # type: ignore[no-untyped-def]
        if v:
            return v
        d = info.data
        return (
            f"postgresql+psycopg://{d['DB_USER']}:{d['DB_PASSWORD']}"
            f"@{d['DB_HOST']}:{d['DB_PORT']}/{d['DB_NAME']}"
        )

    @property
    def is_prod(self) -> bool:
        return self.APP_ENV == "prod"

    @property
    def cookie_secure(self) -> bool:
        # Secure cookies require HTTPS. Disabled only in local dev so the cookie
        # works over http://localhost.
        return self.APP_ENV != "dev"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    def validate_for_prod(self) -> None:
        """Fail fast if the app is about to run in prod with insecure defaults."""
        if not self.is_prod:
            return
        problems: list[str] = []
        if "insecure" in self.SESSION_SECRET or len(self.SESSION_SECRET) < 32:
            problems.append("SESSION_SECRET must be a long random value in prod")
        if not self.CREDENTIAL_ENCRYPTION_KEY:
            problems.append(
                "CREDENTIAL_ENCRYPTION_KEY must be set to a generated Fernet key in prod"
            )
        if self.DB_PASSWORD in ("", "algo"):
            problems.append("DB_PASSWORD must not be a default in prod")
        if any("localhost" in o for o in self.cors_origin_list):
            problems.append("CORS_ORIGINS should not include localhost in prod")
        if problems:
            raise RuntimeError(
                "Refusing to start in prod with insecure config:\n  - "
                + "\n  - ".join(problems)
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
