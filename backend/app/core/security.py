"""
================================================================================
security.py  ▸  Password hashing, session tokens, CSRF
================================================================================
Primitives, kept free of any framework coupling so they are unit-testable in
isolation.

Design choices, and why:
  · argon2id for passwords — memory-hard, the current OWASP default.
  · Session tokens are 256 bits of CSPRNG randomness, stored HASHED. The server
    holds only the SHA-256, so a DB dump yields no usable sessions.
  · Constant-time comparison everywhere a secret is checked, to avoid timing
    side channels.
================================================================================
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

# Tuned per OWASP guidance: 19 MiB memory, 2 iterations, 1 lane. Raise on
# stronger hardware; these need to match at verify time only via the stored
# parameters (argon2 encodes them in the hash), so bumping them is safe.
_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=19 * 1024,
    parallelism=1,
)

_TOKEN_BYTES = 32  # 256 bits


# ── Passwords ───────────────────────────────────────────────────────────────
def hash_password(plaintext: str) -> str:
    return _hasher.hash(plaintext)


def verify_password(plaintext: str, stored_hash: str) -> bool:
    """Return True iff the password matches. Never raises on a bad password."""
    try:
        return _hasher.verify(stored_hash, plaintext)
    except (VerifyMismatchError, InvalidHashError, Exception):  # noqa: BLE001
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True when the hash was made with weaker params and should be upgraded."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except Exception:  # noqa: BLE001
        return False


# ── Session tokens ───────────────────────────────────────────────────────────
def generate_session_token() -> str:
    """A fresh opaque session token. Given to the client, never stored raw."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """SHA-256 of a token, for storage and lookup.

    A plain hash (not argon2) is correct here: the token is already high-entropy
    random, so it is not brute-forceable, and lookups must be fast.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ── CSRF ─────────────────────────────────────────────────────────────────────
def generate_csrf_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# ── Credential encryption at rest ─────────────────────────────────────────────
# Broker credentials (API key/secret, MPIN, TOTP secret) are secrets that let us
# act on a user's real trading account. They are encrypted with Fernet
# (AES-128-CBC + HMAC) BEFORE storage, so a database leak alone cannot be used
# to log in to anyone's broker.

@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    """The process-wide Fernet cipher.

    Uses CREDENTIAL_ENCRYPTION_KEY when set. In dev, where it is empty, the key
    is derived deterministically from SESSION_SECRET so the app runs without
    extra setup — prod refuses to start without an explicit key (see
    Settings.validate_for_prod).
    """
    key = settings.CREDENTIAL_ENCRYPTION_KEY.strip()
    if key:
        return Fernet(key.encode("utf-8"))
    derived = hashlib.sha256(settings.SESSION_SECRET.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret for storage. Returns an opaque urlsafe token string."""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    """Decrypt a value produced by :func:`encrypt_secret`.

    Raises ValueError if the ciphertext is tampered with or the key changed.
    """
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:  # pragma: no cover - integrity failure
        raise ValueError("Could not decrypt stored credential") from exc
