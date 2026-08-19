"""Async database engine, session factory, and FastAPI dependency."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(db_url: str | None = None, echo: bool | None = None) -> AsyncEngine:
    """Retrieve or create the global async SQLAlchemy engine."""
    global _engine
    if _engine is None:
        url = db_url or settings.database.url
        echo_sql = echo if echo is not None else settings.database.echo
        _engine = create_async_engine(
            url,
            echo=echo_sql,
            pool_size=settings.database.pool_size,
            max_overflow=settings.database.max_overflow,
        )
    return _engine


def get_session_factory(engine: AsyncEngine | None = None) -> async_sessionmaker[AsyncSession]:
    """Retrieve or create the async session factory."""
    global _session_factory
    if _session_factory is None:
        eng = engine or get_engine()
        _session_factory = async_sessionmaker(
            bind=eng,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async database session."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
