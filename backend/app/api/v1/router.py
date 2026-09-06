"""Aggregates all v1 routers under a single prefix."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, broker, dashboard, notifications

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(dashboard.router)
api_router.include_router(broker.router)
api_router.include_router(notifications.router)
