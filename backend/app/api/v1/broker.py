"""
================================================================================
api/v1/broker.py  ▸  Broker connection endpoints
================================================================================
    POST /api/v1/broker/connect   → verify credentials LIVE with the broker,
                                     persist them encrypted, advance onboarding
    GET  /api/v1/broker/status    → whether the caller has a live connection

`/connect` performs a real login against the broker (Angel One SmartAPI). A
success response means the broker itself accepted the credentials — there is no
mock/assume-valid path. Both endpoints require an authenticated session and act
only on the caller's own account; the POST is CSRF-protected via CurrentUser.

NOTE: no `from __future__ import annotations` — FastAPI must see real param
types (see auth.py).
================================================================================
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import CurrentUser, DbSession
from app.api.rate_limit import RateLimit
from app.core.config import settings
from app.schemas.broker import (
    BrokerAccountResponse,
    BrokerStatusResponse,
    ConnectBrokerRequest,
    ConnectBrokerResponse,
)
from app.services import broker_service
from app.services.broker_service import BrokerError

log = logging.getLogger("broker.api")

router = APIRouter(prefix="/broker", tags=["broker"])

_STATUS_BY_CODE = {
    "unsupported": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "invalid": status.HTTP_422_UNPROCESSABLE_ENTITY,
    # Credential rejected by the broker → 401 so the frontend shows the message.
    "unauthorized": status.HTTP_401_UNAUTHORIZED,
    # Broker/network transiently down → 503 (retryable).
    "unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
}


def _raise(err: BrokerError) -> None:
    raise HTTPException(
        status_code=_STATUS_BY_CODE.get(err.code, status.HTTP_400_BAD_REQUEST),
        detail=err.message,
    )


@router.post(
    "/connect",
    response_model=ConnectBrokerResponse,
    response_model_by_alias=True,
    dependencies=[Depends(RateLimit(settings.BROKER_CONNECT_RATE_LIMIT))],
)
async def connect(
    payload: ConnectBrokerRequest,
    current: CurrentUser,
    db: DbSession,
) -> ConnectBrokerResponse:
    try:
        conn = await broker_service.connect_broker(
            db,
            user=current.user,
            broker_id=payload.broker_id,
            credentials=payload.credentials,
        )
        await db.commit()
    except BrokerError as err:
        await db.rollback()
        _raise(err)

    live_margin = getattr(conn, "_live_available_margin", None)
    return ConnectBrokerResponse(
        connected=True,
        broker_id=conn.broker_id,
        masked_client_id=conn.masked_client_id,
        account_name=conn.account_name,
        available_margin=live_margin,
    )


@router.get(
    "/accounts",
    response_model=list[BrokerAccountResponse],
    response_model_by_alias=True,
)
async def broker_accounts(
    current: CurrentUser, db: DbSession
) -> list[BrokerAccountResponse]:
    """Every broker the caller has connected — real BrokerConnection rows."""
    conns = await broker_service.list_connections(db, user_id=current.user.id)
    return [
        BrokerAccountResponse(
            id=str(c.id),
            broker_id=c.broker_id,
            masked_client_id=c.masked_client_id,
            account_name=c.account_name,
            status="connected" if c.is_active else "error",
            connected_at=(
                c.last_verified_at.isoformat() if c.last_verified_at else ""
            ),
        )
        for c in conns
    ]


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_account(
    account_id: str, current: CurrentUser, db: DbSession
) -> None:
    """Disconnect (delete) one of the caller's broker connections."""
    removed = await broker_service.disconnect(
        db, user_id=current.user.id, connection_id=account_id
    )
    if not removed:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Broker account not found"
        )
    await db.commit()


@router.get(
    "/status",
    response_model=BrokerStatusResponse,
    response_model_by_alias=True,
)
async def broker_status(current: CurrentUser, db: DbSession) -> BrokerStatusResponse:
    conn = await broker_service.active_connection(db, user_id=current.user.id)
    if conn is None:
        return BrokerStatusResponse(connected=False)
    return BrokerStatusResponse(
        connected=True,
        broker_id=conn.broker_id,
        masked_client_id=conn.masked_client_id,
        account_name=conn.account_name,
        last_verified_at=conn.last_verified_at.isoformat()
        if conn.last_verified_at
        else None,
    )
