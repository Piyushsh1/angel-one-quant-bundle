"""
================================================================================
api/v1/dashboard.py  ▸  Trading terminal endpoints
================================================================================
Every route requires an authenticated session (CurrentUser) and operates ONLY
on the caller's own data. Read endpoints are GET (no CSRF); state-changing
endpoints are POST/PATCH/DELETE and are CSRF-protected by the CurrentUser
dependency.

Endpoints
─────────
  GET    /api/v1/dashboard/metrics            → metric cards + hero
  GET    /api/v1/dashboard/pnl/intraday       → intraday P&L curve
  GET    /api/v1/dashboard/positions          → open positions + aggregate
  POST   /api/v1/dashboard/positions/{id}/close
  POST   /api/v1/dashboard/positions/panic    → square off everything
  GET    /api/v1/dashboard/strategies         → strategy engine list
  PATCH  /api/v1/dashboard/strategies/{id}/status
  GET    /api/v1/dashboard/orders             → recent order ledger
  GET    /api/v1/dashboard/market/indices     → hero index quotes

NOTE: no `from __future__ import annotations` — see the note in auth.py; FastAPI
must see real types on route params.
================================================================================
"""
import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response, StreamingResponse

from app.api.dependencies import CurrentUser, DbSession
from app.core.config import settings
from app.infrastructure.database.session import SessionFactory
from app.services import auth_service
from app.schemas.dashboard import (
    IndexQuoteResponse,
    IntradayPnlResponse,
    MetricsResponse,
    OrderResponse,
    PanicResponse,
    PositionResponse,
    PositionsResponse,
    ReportsResponse,
    RiskParametersResponse,
    RiskParametersUpdate,
    StrategyResponse,
    StrategyStatusUpdate,
)
from app.services import dashboard_service
from app.services.dashboard_service import DashboardError

log = logging.getLogger("dashboard.api")

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _raise(err: DashboardError) -> None:
    code_map = {
        "not_found": status.HTTP_404_NOT_FOUND,
        "invalid": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "bad_request": status.HTTP_400_BAD_REQUEST,
    }
    raise HTTPException(
        status_code=code_map.get(err.code, status.HTTP_400_BAD_REQUEST),
        detail=err.message,
    )


# ── Metrics + hero ───────────────────────────────────────────────────────────

@router.get("/metrics", response_model=MetricsResponse, response_model_by_alias=True)
async def metrics(
    current: CurrentUser, db: DbSession, date: str | None = None
) -> MetricsResponse:
    try:
        data = await dashboard_service.compute_metrics(
            db, user_id=current.user.id, date=date
        )
        # compute_metrics lazily creates the risk-parameters row (for the mode
        # flag) on first access; persist it.
        await db.commit()
    except DashboardError as err:
        _raise(err)
    return MetricsResponse(**data)


# ── Real-time stream (Server-Sent Events) ────────────────────────────────────
# Pushes a fresh metrics + positions snapshot every couple of seconds so the
# terminal reflects auto-engine trades almost the instant they happen, without
# the client polling several endpoints. Auth is by the session cookie (a GET,
# so no CSRF). The stream uses short-lived DB sessions per snapshot — never the
# request-scoped session, which must not be held open for a long-lived stream.

_STREAM_INTERVAL_SEC = 2.0


@router.get("/stream")
async def stream(request: Request):
    raw_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Not authenticated")
    async with SessionFactory() as db:
        resolved = await auth_service.resolve_session(db, raw_token=raw_token)
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Not authenticated")
    user, _session = resolved
    user_id = user.id

    async def event_gen():
        while True:
            if await request.is_disconnected():
                break
            try:
                async with SessionFactory() as db:
                    metrics_data = await dashboard_service.compute_metrics(
                        db, user_id=user_id
                    )
                    pos = await dashboard_service.positions_view(
                        db, user_id=user_id
                    )
                    await db.commit()
                payload = {
                    "metrics": MetricsResponse(**metrics_data).model_dump(
                        by_alias=True
                    ),
                    "positions": {
                        "items": [
                            PositionResponse(**p).model_dump(by_alias=True)
                            for p in pos["items"]
                        ],
                        "aggregateUnrealized": pos["aggregate_unrealized"],
                        "live": pos["live"],
                        "notice": pos["notice"],
                    },
                }
                yield f"data: {json.dumps(payload)}\n\n"
            except Exception:  # noqa: BLE001 — a bad snapshot must not kill the stream
                log.exception("stream snapshot failed")
            await asyncio.sleep(_STREAM_INTERVAL_SEC)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Intraday P&L ─────────────────────────────────────────────────────────────

@router.get(
    "/pnl/intraday",
    response_model=IntradayPnlResponse,
    response_model_by_alias=True,
)
async def pnl_intraday(
    current: CurrentUser, db: DbSession, date: str | None = None
) -> IntradayPnlResponse:
    try:
        data = await dashboard_service.intraday_pnl(
            db, user_id=current.user.id, date=date
        )
        await db.commit()  # persist the live P&L snapshot recorded this poll
    except DashboardError as err:
        _raise(err)
    return IntradayPnlResponse(**data)


# ── Positions ────────────────────────────────────────────────────────────────

@router.get(
    "/positions",
    response_model=PositionsResponse,
    response_model_by_alias=True,
)
async def positions(
    current: CurrentUser, db: DbSession, date: str | None = None
) -> PositionsResponse:
    try:
        data = await dashboard_service.positions_view(
            db, user_id=current.user.id, date=date
        )
    except DashboardError as err:
        _raise(err)
    return PositionsResponse(
        items=[PositionResponse(**p) for p in data["items"]],
        aggregate_unrealized=data["aggregate_unrealized"],
        live=data["live"],
        notice=data["notice"],
    )


@router.post(
    "/positions/{position_id}/close",
    response_model=PositionResponse,
    response_model_by_alias=True,
)
async def close_position(
    position_id: str, current: CurrentUser, db: DbSession
) -> PositionResponse:
    try:
        position = await dashboard_service.close_position(
            db, user_id=current.user.id, position_id=position_id
        )
        await db.commit()
    except DashboardError as err:
        _raise(err)
    log.info("position closed id=%s user=%s", position_id, current.user.email)
    return PositionResponse.model_validate(position)


@router.post(
    "/positions/panic",
    response_model=PanicResponse,
    response_model_by_alias=True,
)
async def panic(current: CurrentUser, db: DbSession) -> PanicResponse:
    """Emergency square-off: close all open positions, cancel pending orders,
    and halt all running strategies."""
    closed = await dashboard_service.close_all_positions(db, user_id=current.user.id)
    cancelled = await dashboard_service.cancel_pending_orders(db, user_id=current.user.id)
    await dashboard_service.halt_all_strategies(db, user_id=current.user.id)
    await db.commit()
    log.warning(
        "PANIC square-off user=%s closed=%d cancelled=%d",
        current.user.email,
        closed,
        cancelled,
    )
    return PanicResponse(
        closed_positions=closed,
        cancelled_orders=cancelled,
        message=(
            f"Kill-switch executed. Closed {closed} positions, "
            f"cancelled {cancelled} pending orders, halted all strategies."
        ),
    )


# ── Strategies ───────────────────────────────────────────────────────────────

@router.get(
    "/strategies",
    response_model=list[StrategyResponse],
    response_model_by_alias=True,
)
async def strategies(current: CurrentUser, db: DbSession) -> list[StrategyResponse]:
    items = await dashboard_service.list_strategies(db, user_id=current.user.id)
    return [StrategyResponse.model_validate(s) for s in items]


@router.patch(
    "/strategies/{strategy_id}/status",
    response_model=StrategyResponse,
    response_model_by_alias=True,
)
async def update_strategy_status(
    strategy_id: str,
    payload: StrategyStatusUpdate,
    current: CurrentUser,
    db: DbSession,
) -> StrategyResponse:
    try:
        strategy = await dashboard_service.set_strategy_status(
            db,
            user_id=current.user.id,
            strategy_id=strategy_id,
            status=payload.status,
        )
        await db.commit()
    except DashboardError as err:
        _raise(err)
    log.info(
        "strategy %s -> %s user=%s", strategy_id, payload.status, current.user.email
    )
    return StrategyResponse.model_validate(strategy)


# ── Orders ───────────────────────────────────────────────────────────────────

@router.get(
    "/orders",
    response_model=list[OrderResponse],
    response_model_by_alias=True,
)
async def orders(
    current: CurrentUser, db: DbSession, date: str | None = None
) -> list[OrderResponse]:
    try:
        data = await dashboard_service.orders_view(
            db, user_id=current.user.id, date=date
        )
    except DashboardError as err:
        _raise(err)
    return [OrderResponse(**o) for o in data["items"]]


# ── Market indices ───────────────────────────────────────────────────────────

@router.get(
    "/market/indices",
    response_model=list[IndexQuoteResponse],
    response_model_by_alias=True,
)
async def market_indices(current: CurrentUser, db: DbSession) -> list[IndexQuoteResponse]:
    data = await dashboard_service.market_indices(db, user_id=current.user.id)
    return [IndexQuoteResponse(**d) for d in data]


# ── Quant reports ────────────────────────────────────────────────────────────

@router.get("/reports", response_model=ReportsResponse, response_model_by_alias=True)
async def reports(
    current: CurrentUser, db: DbSession, date: str | None = None
) -> ReportsResponse:
    try:
        data = await dashboard_service.compute_reports(
            db, user_id=current.user.id, date=date
        )
    except DashboardError as err:
        _raise(err)
    return ReportsResponse(**data)


@router.get("/reports/export.csv")
async def reports_csv(
    current: CurrentUser, db: DbSession, date: str | None = None
) -> Response:
    """Download the current reports payload as a CSV attachment."""
    try:
        data = await dashboard_service.compute_reports(
            db, user_id=current.user.id, date=date
        )
    except DashboardError as err:
        _raise(err)
    csv_text = dashboard_service.reports_to_csv(data)
    stamp = (date or dashboard_service._today())
    filename = f"quant-report-{stamp}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Risk parameters ──────────────────────────────────────────────────────────

@router.get(
    "/risk",
    response_model=RiskParametersResponse,
    response_model_by_alias=True,
)
async def get_risk(current: CurrentUser, db: DbSession) -> RiskParametersResponse:
    params = await dashboard_service.get_risk_parameters(db, user_id=current.user.id)
    await db.commit()  # persist defaults created on first access
    return RiskParametersResponse.model_validate(params)


@router.patch(
    "/risk",
    response_model=RiskParametersResponse,
    response_model_by_alias=True,
)
async def update_risk(
    payload: RiskParametersUpdate, current: CurrentUser, db: DbSession
) -> RiskParametersResponse:
    params = await dashboard_service.update_risk_parameters(
        db,
        user_id=current.user.id,
        changes=payload.model_dump(exclude_unset=True),
    )
    await db.commit()
    log.info("risk parameters updated user=%s", current.user.email)
    return RiskParametersResponse.model_validate(params)
