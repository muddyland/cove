"""Pytest configuration for the Cove backend test-suite.

CRITICAL: the SQLAlchemy engine is created at import time from
``settings.data_dir`` (default ``/app/data``). We must therefore point
``COVE_DATA_DIR`` at a writable temp directory *before any server module is
imported*. We do that here, at the very top of conftest (which pytest imports
before collecting/importing the test modules), using a process-unique temp dir.

We also force ``COVE_COOKIE_SECURE=false`` so the TestClient (plain http)
receives the auth cookies, and ensure no real Docker daemon / network is ever
touched (see the autouse ``_no_docker_no_network`` fixture and per-test
monkeypatches).
"""

import os
import sys
import tempfile
from pathlib import Path

# ── Environment must be set BEFORE importing any server module ─────────────────
_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="cove-test-"))
os.environ["COVE_DATA_DIR"] = str(_TEST_DATA_DIR)
os.environ["COVE_COOKIE_SECURE"] = "false"
# Keep OIDC disabled and the DB unencrypted regardless of the host env.
os.environ.pop("COVE_OIDC_ISSUER", None)
os.environ.pop("COVE_DB_ENCRYPTION_KEY", None)

# Make the backend package importable when pytest is run from anywhere.
_BACKEND_ROOT = Path(__file__).resolve().parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import pytest  # noqa: E402

import server.routers.auth as auth_router  # noqa: E402

# Import server modules only after the env is configured.
from server.db import SessionLocal, engine  # noqa: E402
from server.deps import get_db  # noqa: E402
from server.main import create_app  # noqa: E402
from server.migrations import run_migrations  # noqa: E402


def _reset_database() -> None:
    """Drop and recreate every table so each test starts from a clean slate."""
    import server.models  # noqa: F401  (ensure models are registered on Base)
    from server.db import Base

    Base.metadata.drop_all(engine)
    run_migrations()


@pytest.fixture(autouse=True)
def _clean_db():
    """Give every test a fresh schema + empty rate-limit buckets."""
    _reset_database()
    auth_router._rate_buckets.clear()
    yield


@pytest.fixture(autouse=True)
def _no_logo_network(monkeypatch):
    """Never fetch real project logos when baking watermarked icons — icon bakes
    are a no-op by default so sync/create tests stay hermetic. Tests that exercise
    baking stub ``server.icons._fetch_logo_bytes`` themselves to return real bytes.
    """
    async def _no_fetch(_client, _url):
        return None

    monkeypatch.setattr("server.icons._fetch_logo_bytes", _no_fetch, raising=False)


@pytest.fixture
def db_session():
    """A SQLAlchemy session bound to the temp DB."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def fake_docker_manager():
    """A MagicMock standing in for the real DockerManager."""
    from unittest.mock import MagicMock

    return MagicMock(name="DockerManager")


@pytest.fixture
def client(monkeypatch, fake_docker_manager):
    """A TestClient with Docker fully stubbed out.

    We override ``get_db`` so the app and the test share the same session
    factory (already pointed at the temp DB), and patch
    ``get_docker_manager`` so background tasks never touch a real daemon.
    No TestClient context-manager (lifespan) is used, so the status-monitor /
    catalog-seed background tasks never start.
    """
    app = create_app()

    def _override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db

    # Any endpoint that schedules docker work imports get_docker_manager lazily
    # from server.docker_manager, so patching it there covers every router.
    monkeypatch.setattr(
        "server.docker_manager.get_docker_manager",
        lambda zone_id=0: fake_docker_manager,
    )

    from fastapi.testclient import TestClient

    with_client = TestClient(app)
    return with_client
