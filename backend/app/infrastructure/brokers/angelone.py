"""
================================================================================
brokers/angelone.py  ▸  Angel One SmartAPI client
================================================================================
A thin async client over Angel One's SmartAPI REST endpoints. Its only job here
is REAL-TIME credential verification: it performs an actual login against Angel
One with the user's own API key, client code, MPIN and TOTP secret, then reads
the account profile and RMS (risk-management/margin) so the caller can confirm
the account is genuinely reachable.

There is no mock path and no stored fixture — if Angel One rejects the
credentials (wrong key, wrong client code, wrong MPIN, bad/expired TOTP), the
login fails and we surface that failure. A "connected" state is only ever
reached because Angel One itself accepted the login.

Endpoints and headers follow Angel One's official SmartAPI (the same contract
the angel-one/smartapi-python SDK uses):
    POST /rest/auth/angelbroking/user/v1/loginByPassword
    GET  /rest/secure/angelbroking/user/v1/getProfile
    GET  /rest/secure/angelbroking/user/v1/getRMS
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
import pyotp

from app.core.config import settings

log = logging.getLogger("broker.angelone")

_LOGIN_PATH = "/rest/auth/angelbroking/user/v1/loginByPassword"
_PROFILE_PATH = "/rest/secure/angelbroking/user/v1/getProfile"
_RMS_PATH = "/rest/secure/angelbroking/user/v1/getRMS"
_POSITIONS_PATH = "/rest/secure/angelbroking/order/v1/getPosition"
_HOLDINGS_PATH = "/rest/secure/angelbroking/portfolio/v1/getHolding"
_ORDERBOOK_PATH = "/rest/secure/angelbroking/order/v1/getOrderBook"
_TRADEBOOK_PATH = "/rest/secure/angelbroking/order/v1/getTradeBook"
_LTP_PATH = "/rest/secure/angelbroking/order/v1/getLtpData"
_REFRESH_PATH = "/rest/auth/angelbroking/jwt/v1/generateTokens"
_PLACE_ORDER_PATH = "/rest/secure/angelbroking/order/v1/placeOrder"
_CANDLE_PATH = "/rest/secure/angelbroking/historical/v1/getCandleData"


class BrokerSessionError(Exception):
    """Raised when a stored broker session cannot be used (expired/invalid).

    The caller should treat this as "no live data available" — never fabricate.
    `needs_reconnect` signals the user must re-login (refresh also failed).
    """

    def __init__(self, message: str, *, needs_reconnect: bool = False):
        super().__init__(message)
        self.message = message
        self.needs_reconnect = needs_reconnect


class BrokerAuthError(Exception):
    """Raised when the broker rejects the credentials or is unreachable.

    `retryable` distinguishes a transient network/broker outage (worth a retry)
    from a definite credential rejection (do not retry). `invalid_input` marks a
    malformed input we can reject before/without a broker round-trip (e.g. a
    TOTP secret that isn't valid base32).
    """

    def __init__(
        self, message: str, *, retryable: bool = False, invalid_input: bool = False
    ):
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.invalid_input = invalid_input


@dataclass(frozen=True)
class AngelOneVerification:
    """The outcome of a successful, live-verified Angel One login."""

    client_id: str
    account_name: str | None
    email: str | None
    available_margin: float | None
    # Session tokens returned by the broker. Short-lived; the caller decides
    # whether to persist (encrypted) or discard them.
    jwt_token: str
    refresh_token: str
    feed_token: str | None


def _current_totp(totp_secret: str) -> str:
    """Derive the 6-digit TOTP from the user's base32 secret.

    The user supplies the SECRET (from Angel One's enable-TOTP QR), not a code,
    so we can generate a fresh, non-expired code at the moment of login. A stale
    code is the most common cause of a spurious failure, and this avoids it.
    """
    try:
        return pyotp.TOTP(totp_secret.strip().replace(" ", "")).now()
    except Exception as exc:  # noqa: BLE001 - bad base32 etc.
        raise BrokerAuthError(
            "TOTP secret is not a valid base32 secret. Copy the secret shown "
            "under the QR code when enabling TOTP, not the 6-digit code.",
            invalid_input=True,
        ) from exc


def _base_headers(api_key: str) -> dict[str, str]:
    # Angel One requires these headers on every call. The IP/MAC values are
    # informational for their audit; we send stable non-identifying defaults.
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-UserType": "USER",
        "X-SourceID": "WEB",
        "X-ClientLocalIP": "127.0.0.1",
        "X-ClientPublicIP": "127.0.0.1",
        "X-MACAddress": "00:00:00:00:00:00",
        "X-PrivateKey": api_key,
    }


def _extract_error(payload: dict) -> str:
    msg = payload.get("message") or payload.get("errorcode") or "Login rejected by Angel One"
    return str(msg)


async def verify_credentials(
    *,
    api_key: str,
    client_id: str,
    mpin: str,
    totp_secret: str,
) -> AngelOneVerification:
    """Perform a REAL login against Angel One and return the verified account.

    Raises BrokerAuthError on any failure. This is the single source of truth
    for whether an Angel One demat account connection is valid.
    """
    totp = _current_totp(totp_secret)
    headers = _base_headers(api_key)

    timeout = httpx.Timeout(settings.ANGELONE_HTTP_TIMEOUT)
    async with httpx.AsyncClient(
        base_url=settings.ANGELONE_API_BASE_URL, timeout=timeout
    ) as client:
        # 1) Live login — this is the actual credential validation.
        try:
            resp = await client.post(
                _LOGIN_PATH,
                headers=headers,
                json={
                    "clientcode": client_id.strip(),
                    "password": mpin.strip(),  # Angel One's field is "password"; it is the MPIN
                    "totp": totp,
                },
            )
        except httpx.HTTPError as exc:
            raise BrokerAuthError(
                "Could not reach Angel One. Check your connection and try again.",
                retryable=True,
            ) from exc

        if resp.status_code >= 500:
            raise BrokerAuthError(
                "Angel One is temporarily unavailable. Please retry shortly.",
                retryable=True,
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise BrokerAuthError(
                "Angel One returned an unexpected response.", retryable=True
            ) from exc

        if not data.get("status") or "data" not in data or data["data"] is None:
            # A definite rejection: wrong API key, client code, MPIN, or TOTP.
            raise BrokerAuthError(_extract_error(data))

        tokens = data["data"]
        jwt_token = tokens.get("jwtToken")
        refresh_token = tokens.get("refreshToken")
        feed_token = tokens.get("feedToken")
        if not jwt_token:
            raise BrokerAuthError(_extract_error(data))

        auth_headers = {**headers, "Authorization": f"Bearer {jwt_token}"}

        # 2) Profile — confirms the token works and yields the account name.
        account_name: str | None = None
        email: str | None = None
        verified_client_id = client_id.strip()
        try:
            prof_resp = await client.get(_PROFILE_PATH, headers=auth_headers)
            prof = prof_resp.json()
            if prof.get("status") and prof.get("data"):
                pdata = prof["data"]
                account_name = pdata.get("name")
                email = pdata.get("email")
                verified_client_id = pdata.get("clientcode") or verified_client_id
        except (httpx.HTTPError, ValueError):
            # The login already succeeded; a profile hiccup should not fail the
            # connection. We just proceed without the display name.
            log.warning("Angel One profile fetch failed after successful login")

        # 3) RMS / margin — the live available margin on the account.
        available_margin: float | None = None
        try:
            rms_resp = await client.get(_RMS_PATH, headers=auth_headers)
            rms = rms_resp.json()
            if rms.get("status") and rms.get("data"):
                available_margin = _to_float(rms["data"].get("availablecash"))
        except (httpx.HTTPError, ValueError):
            log.warning("Angel One RMS fetch failed after successful login")

    log.info("Angel One login verified for client=%s", verified_client_id)
    return AngelOneVerification(
        client_id=verified_client_id,
        account_name=account_name,
        email=email,
        available_margin=available_margin,
        jwt_token=jwt_token,
        refresh_token=refresh_token or "",
        feed_token=feed_token,
    )


def _to_float(value) -> float | None:  # type: ignore[no-untyped-def]
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  Authenticated live-data calls (used AFTER connect, with the stored session)
# ══════════════════════════════════════════════════════════════════════════════
#  These power the real-time dashboard. Each takes the stored jwt token (and the
#  api key, needed in the X-PrivateKey header). On a 401/expired token they raise
#  BrokerSessionError so the caller can attempt a refresh or fall back to an
#  honest empty state — they NEVER invent data.


def _auth_headers(api_key: str, jwt_token: str) -> dict[str, str]:
    return {**_base_headers(api_key), "Authorization": f"Bearer {jwt_token}"}


async def _authed_get(
    client: httpx.AsyncClient, path: str, *, api_key: str, jwt_token: str
) -> dict:
    """GET a secure endpoint and return the parsed body, raising on auth failure."""
    try:
        resp = await client.get(path, headers=_auth_headers(api_key, jwt_token))
    except httpx.HTTPError as exc:
        raise BrokerSessionError(
            "Could not reach Angel One.", needs_reconnect=False
        ) from exc

    if resp.status_code in (401, 403):
        raise BrokerSessionError("Broker session expired.", needs_reconnect=True)

    try:
        data = resp.json()
    except ValueError as exc:
        raise BrokerSessionError("Angel One returned an unexpected response.") from exc

    # Angel One signals an auth problem in-body too (status false + token msg).
    # But `status:false` also occurs for benign "no data" cases (e.g. an account
    # with no open positions), so only treat it as a session error when the
    # message clearly indicates auth — otherwise return the (empty) body.
    if not data.get("status"):
        msg = str(data.get("message") or "").lower()
        code = str(data.get("errorcode") or "")
        log.info("Angel One %s status=false code=%s msg=%s", path, code, msg)
        auth_words = ("token", "expired", "invalid token", "unauthor", "session")
        # AG8001/AG8002 are Angel One's token/invalid-session error codes.
        if code in ("AG8001", "AG8002") or any(w in msg for w in auth_words):
            raise BrokerSessionError(
                data.get("message") or "Session invalid", needs_reconnect=True
            )
    return data


async def refresh_jwt(*, api_key: str, refresh_token: str) -> str | None:
    """Mint a fresh JWT from a refresh token. Returns the new JWT, or None."""
    timeout = httpx.Timeout(settings.ANGELONE_HTTP_TIMEOUT)
    async with httpx.AsyncClient(
        base_url=settings.ANGELONE_API_BASE_URL, timeout=timeout
    ) as client:
        try:
            resp = await client.post(
                _REFRESH_PATH,
                headers=_base_headers(api_key),
                json={"refreshToken": refresh_token},
            )
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
    if data.get("status") and data.get("data"):
        return data["data"].get("jwtToken")
    return None


async def fetch_rms(*, api_key: str, jwt_token: str) -> dict:
    """Live RMS (risk-management) — available cash, margin, utilised, etc."""
    timeout = httpx.Timeout(settings.ANGELONE_HTTP_TIMEOUT)
    async with httpx.AsyncClient(
        base_url=settings.ANGELONE_API_BASE_URL, timeout=timeout
    ) as client:
        data = await _authed_get(client, _RMS_PATH, api_key=api_key, jwt_token=jwt_token)
    return data.get("data") or {}


async def fetch_positions(*, api_key: str, jwt_token: str) -> list[dict]:
    """Live intraday/net positions from the broker's position book."""
    timeout = httpx.Timeout(settings.ANGELONE_HTTP_TIMEOUT)
    async with httpx.AsyncClient(
        base_url=settings.ANGELONE_API_BASE_URL, timeout=timeout
    ) as client:
        data = await _authed_get(
            client, _POSITIONS_PATH, api_key=api_key, jwt_token=jwt_token
        )
    return data.get("data") or []


async def fetch_holdings(*, api_key: str, jwt_token: str) -> list[dict]:
    """Live demat holdings (delivery)."""
    timeout = httpx.Timeout(settings.ANGELONE_HTTP_TIMEOUT)
    async with httpx.AsyncClient(
        base_url=settings.ANGELONE_API_BASE_URL, timeout=timeout
    ) as client:
        data = await _authed_get(
            client, _HOLDINGS_PATH, api_key=api_key, jwt_token=jwt_token
        )
    payload = data.get("data")
    # getHolding may return {holdings:[...], totalholding:{...}} or a bare list.
    if isinstance(payload, dict):
        return payload.get("holdings") or []
    return payload or []


async def fetch_orderbook(*, api_key: str, jwt_token: str) -> list[dict]:
    """Live order book — every order placed today with its status."""
    timeout = httpx.Timeout(settings.ANGELONE_HTTP_TIMEOUT)
    async with httpx.AsyncClient(
        base_url=settings.ANGELONE_API_BASE_URL, timeout=timeout
    ) as client:
        data = await _authed_get(
            client, _ORDERBOOK_PATH, api_key=api_key, jwt_token=jwt_token
        )
    return data.get("data") or []


async def fetch_ltp(
    *, api_key: str, jwt_token: str, exchange: str, symbol: str, token: str
) -> dict | None:
    """Live LTP + OHLC for one instrument, used for the index ticker."""
    timeout = httpx.Timeout(settings.ANGELONE_HTTP_TIMEOUT)
    async with httpx.AsyncClient(
        base_url=settings.ANGELONE_API_BASE_URL, timeout=timeout
    ) as client:
        try:
            resp = await client.post(
                _LTP_PATH,
                headers=_auth_headers(api_key, jwt_token),
                json={
                    "exchange": exchange,
                    "tradingsymbol": symbol,
                    "symboltoken": token,
                },
            )
            if resp.status_code in (401, 403):
                raise BrokerSessionError("Broker session expired.",
                                         needs_reconnect=True)
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
    if data.get("status") and data.get("data"):
        return data["data"]
    return None


async def place_order(
    *, api_key: str, jwt_token: str, order: dict
) -> dict:
    """Place a REAL order on Angel One. Returns the broker's order response.

    This is the ONLY function that sends a live order to the broker. Callers
    must gate it on the user's live-trading switch (see broker_live_service);
    it performs no mode check itself. Raises BrokerSessionError on an expired
    session, BrokerAuthError on rejection.
    """
    timeout = httpx.Timeout(settings.ANGELONE_HTTP_TIMEOUT)
    async with httpx.AsyncClient(
        base_url=settings.ANGELONE_API_BASE_URL, timeout=timeout
    ) as client:
        try:
            resp = await client.post(
                _PLACE_ORDER_PATH,
                headers=_auth_headers(api_key, jwt_token),
                json=order,
            )
        except httpx.HTTPError as exc:
            raise BrokerSessionError(
                "Could not reach Angel One to place the order.",
            ) from exc

        if resp.status_code in (401, 403):
            raise BrokerSessionError("Broker session expired.", needs_reconnect=True)

        try:
            data = resp.json()
        except ValueError as exc:
            raise BrokerAuthError(
                "Angel One returned an unexpected response to the order."
            ) from exc

    if not data.get("status") or not data.get("data"):
        raise BrokerAuthError(
            str(data.get("message") or "Order rejected by Angel One")
        )
    return data["data"]


async def fetch_candles(
    *,
    api_key: str,
    jwt_token: str,
    exchange: str,
    symbol_token: str,
    interval: str,
    from_dt: str,
    to_dt: str,
) -> list[list]:
    """Historical OHLCV candles. Returns Angel One's raw data rows.

    Each row is ``[timestamp, open, high, low, close, volume]``. Times are
    ``"YYYY-MM-DD HH:MM"`` in IST. Interval e.g. ``ONE_MINUTE``, ``FIVE_MINUTE``.
    Returns an empty list on any failure (never raises) so the strategy loop
    degrades to "no signal this cycle" rather than crashing.
    """
    timeout = httpx.Timeout(settings.ANGELONE_HTTP_TIMEOUT)
    try:
        async with httpx.AsyncClient(
            base_url=settings.ANGELONE_API_BASE_URL, timeout=timeout
        ) as client:
            resp = await client.post(
                _CANDLE_PATH,
                headers=_auth_headers(api_key, jwt_token),
                json={
                    "exchange": exchange,
                    "symboltoken": symbol_token,
                    "interval": interval,
                    "fromdate": from_dt,
                    "todate": to_dt,
                },
            )
            if resp.status_code in (401, 403):
                raise BrokerSessionError("Broker session expired.",
                                         needs_reconnect=True)
            data = resp.json()
    except BrokerSessionError:
        raise
    except (httpx.HTTPError, ValueError):
        return []
    if not data.get("status") or not data.get("data"):
        return []
    return data["data"]
