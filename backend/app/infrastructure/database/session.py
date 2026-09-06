"""
================================================================================
session.py  ▸  Async database engine and per-request sessions
================================================================================
This is the concurrency backbone. FastAPI serves requests on an async event
loop; each request gets its OWN `AsyncSession` from the pool, used and closed
within that request. Two users hitting the API at the same moment therefore
never share a session or a transaction — which is precisely what "no conflict
between users" requires at the data layer.

Isolation guarantees:
  · One AsyncSession per request (never shared across requests or users).
  · The connection pool hands each request a distinct connection.
  · Every write commits or rolls back within its own request scope.
================================================================================
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    # pool_pre_ping avoids handing a request a connection the DB has already
    # dropped (common after an idle period or a DB restart).
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=5,
    pool_timeout=15,
    echo=False,
)

# expire_on_commit=False so returned ORM objects stay usable after the request's
# commit, which matters when we serialise a User into the response.
SessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields a request-scoped session and always closes it.

    Rolls back on any unhandled exception so a failed request never leaks a
    half-applied transaction into the next one on that pooled connection.
    """
    async with SessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
