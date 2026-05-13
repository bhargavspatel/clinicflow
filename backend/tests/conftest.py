"""
Shared pytest fixtures for ClinicFlow tests.

Session-scoped DB setup is sync (asyncio.run) so it doesn't conflict with
pytest-asyncio's per-function event loop.  All async fixtures are
function-scoped and therefore work correctly with asyncio_mode="auto".
"""
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DB_URL = (
    "postgresql+asyncpg://clinicflow:clinicflow@postgres:5432/clinicflow_test"
)


# ── One-time DB setup (sync, session-scoped) ──────────────────────────────────

@pytest.fixture(scope="session")
def _setup_test_db() -> None:
    """Create clinicflow_test and materialise the ORM schema (runs once per session)."""
    async def _run() -> None:
        import asyncpg  # already a transitive dep via asyncpg
        import app.models  # noqa: F401 — register all ORM models with Base
        from app.core.database import Base

        root = await asyncpg.connect(
            "postgresql://clinicflow:clinicflow@postgres:5432/clinicflow"
        )
        exists = await root.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", "clinicflow_test"
        )
        if not exists:
            await root.execute("CREATE DATABASE clinicflow_test")
        await root.close()

        engine = create_async_engine(TEST_DB_URL)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_run())


# ── Per-test HTTP client (function-scoped) ────────────────────────────────────

@pytest.fixture
async def client(_setup_test_db: None) -> AsyncClient:
    """
    httpx AsyncClient wired to the test DB.
    get_db is overridden so every request uses clinicflow_test.
    get_arq_pool is overridden with an AsyncMock so tests never need Redis.
    Tables are truncated before each test to provide a clean slate.
    """
    from unittest.mock import AsyncMock

    from app.core.arq_pool import get_arq_pool
    from app.core.database import get_db
    from app.main import app

    engine = create_async_engine(TEST_DB_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as s:
        await s.execute(text("TRUNCATE tenants CASCADE"))
        await s.commit()

    async def _override_get_db() -> AsyncSession:  # type: ignore[return]
        async with factory() as session:
            yield session

    mock_arq = AsyncMock()

    async def _override_arq_pool() -> AsyncMock:
        return mock_arq

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_arq_pool] = _override_arq_pool
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()
    await engine.dispose()


# ── Per-test direct DB session (function-scoped) ─────────────────────────────

@pytest.fixture
async def seed_db(_setup_test_db: None) -> AsyncSession:  # type: ignore[return]
    """
    Direct AsyncSession against clinicflow_test.
    The `clinicflow` role is the DB superuser → bypasses RLS for seeding
    and post-request state assertions.
    """
    engine = create_async_engine(TEST_DB_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()
