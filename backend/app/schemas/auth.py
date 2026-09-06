"""
================================================================================
schemas/auth.py  ▸  Request/response contracts
================================================================================
These MUST match the frontend's Zod schemas
(frontend/src/features/auth/schemas/auth.schema.ts). The response models are the
authoritative shape of what the client parses; drift here becomes a client-side
parse error, which is why the field names use the frontend's camelCase via
aliases.

Password policy is enforced here AND on the client. The client copy is UX; this
copy is the real boundary — never trust client validation for security.
================================================================================
"""
from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# ── Requests ────────────────────────────────────────────────────────────────

_LOWER = re.compile(r"[a-z]")
_UPPER = re.compile(r"[A-Z]")
_DIGIT = re.compile(r"\d")


def _validate_strong_password(value: str) -> str:
    if len(value) < 12:
        raise ValueError("Use at least 12 characters")
    if len(value) > 128:
        raise ValueError("Password is too long")
    if not _LOWER.search(value):
        raise ValueError("Include a lowercase letter")
    if not _UPPER.search(value):
        raise ValueError("Include an uppercase letter")
    if not _DIGIT.search(value):
        raise ValueError("Include a number")
    return value


class SignupRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    full_name: str = Field(alias="fullName", min_length=2, max_length=80)
    email: EmailStr
    password: str
    accepted_terms: bool = Field(alias="acceptedTerms")

    @field_validator("full_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Enter your full name")
        return v

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        return _validate_strong_password(v)

    @field_validator("accepted_terms")
    @classmethod
    def _must_accept(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("You must accept the terms and risk disclosure")
        return v


class LoginRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    email: EmailStr
    password: str
    remember_device: bool = Field(default=False, alias="rememberDevice")

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, v: str) -> str:
        return v.strip().lower()


# ── Responses (camelCase to match the frontend) ───────────────────────────────

class UserResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: str
    email: str
    full_name: str = Field(serialization_alias="fullName")
    has_broker_connected: bool = Field(serialization_alias="hasBrokerConnected")
    has_telegram_linked: bool = Field(serialization_alias="hasTelegramLinked")
    created_at: str = Field(serialization_alias="createdAt")


class AuthResponse(BaseModel):
    """Returned by signup and login. `nextStep` drives client routing."""

    user: UserResponse
    next_step: str = Field(serialization_alias="nextStep")


def user_to_response(user) -> UserResponse:  # type: ignore[no-untyped-def]
    """Map an ORM User to the wire shape, formatting UUID/datetime as strings."""
    return UserResponse(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        has_broker_connected=user.has_broker_connected,
        has_telegram_linked=user.has_telegram_linked,
        created_at=user.created_at.isoformat() if isinstance(user.created_at, datetime) else str(user.created_at),
    )


def compute_next_step(user) -> str:  # type: ignore[no-untyped-def]
    """Server owns onboarding routing so the client cannot desync from it."""
    if not user.has_broker_connected:
        return "connect_broker"
    if not user.has_telegram_linked:
        return "link_telegram"
    return "dashboard"
