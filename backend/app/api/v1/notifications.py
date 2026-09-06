"""
================================================================================
api/v1/notifications.py  ▸  Telegram notification endpoints
================================================================================
    POST /api/v1/notifications/telegram/link    → issue a one-time link code,
                                                   return the bot deep link
    GET  /api/v1/notifications/telegram/status   → whether the caller is linked

The actual chat_id capture happens out-of-band: the user opens the deep link and
taps Start, and the background polling worker (see infrastructure/telegram/
worker.py) binds their chat to their account. The frontend polls `/status` until
`linked` flips true.

Both endpoints require an authenticated session and act only on the caller's own
account.

NOTE: no `from __future__ import annotations` — FastAPI must see real param
types (see auth.py / dependencies.py).
================================================================================
"""
import logging

from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import CurrentUser, DbSession
from app.schemas.notification import (
    LinkTelegramRequest,
    LinkTelegramResponse,
    TelegramStatusResponse,
)
from app.services import notification_service
from app.services.notification_service import NotificationError

log = logging.getLogger("notifications.api")

router = APIRouter(prefix="/notifications", tags=["notifications"])

_STATUS_BY_CODE = {
    "unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "invalid": status.HTTP_422_UNPROCESSABLE_ENTITY,
}


@router.post(
    "/telegram/link",
    response_model=LinkTelegramResponse,
    response_model_by_alias=True,
)
async def link_telegram(
    payload: LinkTelegramRequest,
    current: CurrentUser,
    db: DbSession,
) -> LinkTelegramResponse:
    try:
        result = await notification_service.begin_link(
            db, user=current.user, phone=payload.phone
        )
        await db.commit()
    except NotificationError as err:
        await db.rollback()
        raise HTTPException(
            status_code=_STATUS_BY_CODE.get(err.code, status.HTTP_400_BAD_REQUEST),
            detail=err.message,
        )

    return LinkTelegramResponse(
        linked=bool(result["linked"]),
        deep_link=str(result["deepLink"]),
        bot_username=str(result["botUsername"]),
    )


@router.get(
    "/telegram/status",
    response_model=TelegramStatusResponse,
    response_model_by_alias=True,
)
async def telegram_status(
    current: CurrentUser, db: DbSession
) -> TelegramStatusResponse:
    result = await notification_service.link_status(db, user_id=current.user.id)
    return TelegramStatusResponse(linked=result["linked"])
