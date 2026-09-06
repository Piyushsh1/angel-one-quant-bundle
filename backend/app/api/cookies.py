"""Cookie helpers, centralised so every auth cookie uses identical security flags."""
from __future__ import annotations

from fastapi import Response

from app.core.config import settings

# SameSite=Lax is the right default: it sends the cookie on top-level
# navigations (so returning from an OAuth redirect works) but not on cross-site
# subrequests. Combined with the CSRF double-submit check, this covers the
# common CSRF vectors without breaking the SPA.
_SAMESITE = "lax"


def set_session_cookies(
    response: Response, *, session_token: str, csrf_token: str, max_age: int
) -> None:
    # httpOnly: JavaScript cannot read the session token, so an XSS cannot
    # exfiltrate it. This is why the token is a cookie and not a JSON field.
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=session_token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=_SAMESITE,
        path="/",
    )
    # CSRF cookie is deliberately readable by JS (httpOnly=False) so the SPA can
    # echo it back in the X-CSRF-Token header — the double-submit pattern.
    response.set_cookie(
        key=settings.CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite=_SAMESITE,
        path="/",
    )


def clear_session_cookies(response: Response) -> None:
    for name in (settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME):
        response.delete_cookie(key=name, path="/", samesite=_SAMESITE)
