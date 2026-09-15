"""Configures the real-broker integration tests to run against a fully
isolated, DISPOSABLE Postgres + Redis (see ../../docker-compose.integration.yml),
never the development database or the dev/eager-test-suite's broker
settings. Auto-skips every test in this directory (at collection time, so
even module-scoped fixtures like a real worker never try to connect) if that
disposable infrastructure isn't reachable, rather than erroring the whole
collection or -- far worse -- silently falling back to a database or broker
that could contain real data.

IMPORTANT: every override this file applies (broker URL/timeouts, the ORM's
engine/session factory) is scoped to the duration of a single test via the
`_use_disposable_integration_infra` fixture below, with the prior state
restored afterward -- NOT left mutated at module-import time. Running
`pytest tests/integration -v` on its own doesn't care either way, but running
the FULL suite in one process (`pytest -q`) does: other test files import
`celery_app`/`database` too, and a permanent module-level mutation here was
found (by actually running the full suite once, real infra available) to
silently redirect ALL later tests in the session onto the disposable
Postgres/real broker instead of the intended SQLite/fake-broker test
settings -- exactly the kind of bug this milestone's "unverified until
actually run" stance exists to catch.
"""
import os

import pytest
from dotenv import dotenv_values

# Defaults match docker-compose.integration.yml exactly. Never defaults to
# the app's own .env / DATABASE_URL / CELERY_BROKER_URL -- those are for the
# dev server and the eager unit-test suite (which already overrides
# DATABASE_URL to a local sqlite file; see the root tests/conftest.py) and
# must never be reachable from here regardless of import order.
INTEGRATION_DATABASE_URL = os.getenv(
    "INTEGRATION_DATABASE_URL", "postgresql://postgres:postgres@localhost:55432/b2b_agent_integration"
)
INTEGRATION_CELERY_BROKER_URL = os.getenv("INTEGRATION_CELERY_BROKER_URL", "redis://localhost:6380/0")

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_DEV_ENV_PATH = os.path.join(_REPO_ROOT, ".env")


def _dev_database_url() -> str:
    """Reads the dev DATABASE_URL straight from the repo's .env file (NOT
    from os.environ, which the root test conftest has already overwritten to
    a sqlite path by the time this module loads) -- this is what the safety
    check below compares the integration URL against."""
    try:
        values = dotenv_values(_DEV_ENV_PATH)
        return values.get("DATABASE_URL", "")
    except Exception:
        return ""


def _same_target(url_a: str, url_b: str) -> bool:
    """Compares host+port+path only (ignores scheme/credentials), so e.g.
    differing usernames don't mask two URLs that actually point at the same
    server+database."""
    from urllib.parse import urlsplit

    a, b = urlsplit(url_a), urlsplit(url_b)
    return bool(a.hostname) and (a.hostname, a.port, a.path) == (b.hostname, b.port, b.path)


_dev_url = _dev_database_url()
if _dev_url and _same_target(INTEGRATION_DATABASE_URL, _dev_url):
    # Hard failure, not a skip: this is a misconfiguration that could destroy
    # real data (these tests drop/recreate schema per test run) and must
    # never be allowed to proceed quietly.
    raise RuntimeError(
        "INTEGRATION_DATABASE_URL resolves to the SAME host/port/database as your "
        "development .env's DATABASE_URL. Refusing to run -- these tests drop and "
        "recreate all tables. Point INTEGRATION_DATABASE_URL at the disposable "
        "database from docker-compose.integration.yml instead."
    )


def _postgres_reachable(url: str) -> bool:
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(url, connect_args={"connect_timeout": 2})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


def _redis_reachable(url: str) -> bool:
    try:
        import redis

        client = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
        client.ping()
        return True
    except Exception:
        return False


def _reset_broker_connection_pools(app) -> None:
    """Discards Celery's cached connection/producer pools so the NEXT
    publish or ping actually opens a fresh connection reflecting the
    CURRENT `app.conf.broker_url`, instead of reusing an already-open
    pooled connection to whichever broker was configured when the pool was
    first created.

    This is not a hypothetical concern: `Celery.pool` and
    `AMQP.producer_pool` are both memoized on first access, keyed off
    `app.connection_for_write()` at that moment -- reassigning
    `app.conf.broker_url` afterward does NOT invalidate them. Without this
    reset, restoring the fake broker settings after an integration test
    still left later tests silently publishing over the pooled REAL
    connection to the (still-running) integration Redis container, exactly
    once observed by actually running the full suite together."""
    app._pool = None
    app.amqp._producer_pool = None


def _apply_broker_override(app) -> None:
    """Points `app` at the disposable integration Redis AND restores a
    normal connection timeout. Caller is responsible for restoring the
    previous values afterward (see `_use_disposable_integration_infra`).

    Two separate landmines here, both only discoverable by actually running
    against a real broker (which is exactly why this milestone treats that
    as unverified until it happens):

    1. The root tests/conftest.py (loaded for the whole session, including
       this directory) deliberately sets CELERY_BROKER_CONNECT_TIMEOUT_SECONDS
       to 0.2s so its own eager/unit tests fail fast against an intentionally
       unreachable fake broker. That gets baked into `app.conf` as soon as
       celery_app is first imported anywhere in the session -- too tight for
       a real Docker-forwarded TCP connection.
    2. Whenever CELERY_BROKER_URL is present in os.environ (which the same
       root conftest.py also sets, to a deliberately-unreachable fake
       broker), Celery's Settings resolves `app.conf.broker_url` from that
       environment variable LIVE on every access -- plain attribute
       assignment (`app.conf.broker_url = X`) silently has no effect while
       the env var is set. This was empirically verified (not assumed) by
       comparing `app.conf.broker_url` before/after assignment with and
       without the env var present. The only reliable fix is to overwrite
       the env var itself, not just the conf object.
    """
    os.environ["CELERY_BROKER_URL"] = INTEGRATION_CELERY_BROKER_URL
    app.conf.broker_url = INTEGRATION_CELERY_BROKER_URL
    app.conf.broker_connection_timeout = 5
    app.conf.broker_transport_options = {"socket_connect_timeout": 5, "socket_timeout": 5}
    _reset_broker_connection_pools(app)


def _worker_reachable() -> bool:
    """Confirms a REAL, separate worker process (the worker-integration
    Linux container) is actually consuming from the broker -- not just that
    Redis itself answers a PING. These tests must exercise genuine
    cross-process task execution, never fall back to running tasks natively
    on the host, so this check (a Celery control-plane ping, answered only
    by a live worker) gates the whole suite.

    Saves and restores the env var / conf it touches -- this runs at
    collection time, before pytest_collection_modifyitems decides whether to
    skip anything, and must not leak its probe settings into whichever test
    runs first regardless of the outcome."""
    prior_env = os.environ.get("CELERY_BROKER_URL")
    try:
        import celery_app as _celery_app_probe

        prior_conf = (
            _celery_app_probe.app.conf.broker_url,
            _celery_app_probe.app.conf.broker_connection_timeout,
            _celery_app_probe.app.conf.broker_transport_options,
        )
        try:
            _apply_broker_override(_celery_app_probe.app)
            replies = _celery_app_probe.app.control.ping(timeout=3)
            return bool(replies)
        except Exception:
            return False
        finally:
            (
                _celery_app_probe.app.conf.broker_url,
                _celery_app_probe.app.conf.broker_connection_timeout,
                _celery_app_probe.app.conf.broker_transport_options,
            ) = prior_conf
            _reset_broker_connection_pools(_celery_app_probe.app)
    finally:
        if prior_env is None:
            os.environ.pop("CELERY_BROKER_URL", None)
        else:
            os.environ["CELERY_BROKER_URL"] = prior_env


REDIS_AVAILABLE = _redis_reachable(INTEGRATION_CELERY_BROKER_URL)
POSTGRES_AVAILABLE = _postgres_reachable(INTEGRATION_DATABASE_URL)
WORKER_AVAILABLE = REDIS_AVAILABLE and _worker_reachable()
INFRA_AVAILABLE = REDIS_AVAILABLE and POSTGRES_AVAILABLE and WORKER_AVAILABLE


def _reason() -> str:
    missing = []
    if not POSTGRES_AVAILABLE:
        missing.append(f"Postgres at {INTEGRATION_DATABASE_URL}")
    if not REDIS_AVAILABLE:
        missing.append(f"Redis at {INTEGRATION_CELERY_BROKER_URL}")
    elif not WORKER_AVAILABLE:
        missing.append("a live worker-integration container (Redis answered, but no worker replied to a control ping)")
    return (
        f"Integration infra unreachable: {', '.join(missing)}. Start it with "
        f"`docker compose -f docker-compose.integration.yml up -d --build redis-integration postgres-integration worker-integration` "
        f"from the repo root -- see tests/integration/README.md."
    )


def pytest_collection_modifyitems(config, items):
    """Skips every test in this directory at COLLECTION time (before any
    fixture -- including the module-scoped real-worker fixture, which would
    otherwise try to connect and error out rather than skip cleanly)."""
    if INFRA_AVAILABLE:
        return
    skip_marker = pytest.mark.skip(reason=_reason())
    for item in items:
        if "integration" in item.fspath.dirname.split(os.sep):
            item.add_marker(skip_marker)


@pytest.fixture(autouse=True)
def _use_disposable_integration_infra():
    """For the duration of ONE test: points the ORM and the Celery broker at
    the disposable integration infra, resets the disposable schema, then
    restores everything exactly as it was -- so running these tests
    alongside the rest of the suite in one `pytest -q` process can never
    leave later, unrelated tests silently running against the wrong
    database or broker settings."""
    if not INFRA_AVAILABLE:
        yield
        return

    import celery_app
    import database
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    prior_env = os.environ.get("CELERY_BROKER_URL")
    prior_conf = (
        celery_app.app.conf.broker_url,
        celery_app.app.conf.broker_connection_timeout,
        celery_app.app.conf.broker_transport_options,
    )
    prior_engine = database.engine
    prior_session_local = database.SessionLocal

    _apply_broker_override(celery_app.app)

    # Every module in the app (job_service, tasks, research_service, ...)
    # accesses these via `database.SessionLocal()` / `database.engine`
    # (module-attribute lookups at call time, never `from database import
    # SessionLocal`), so reassigning them here takes effect everywhere,
    # including inside the real worker container, which is configured with
    # this same INTEGRATION_DATABASE_URL directly in its own environment
    # (see docker-compose.integration.yml).
    integration_engine = create_engine(INTEGRATION_DATABASE_URL)
    database.engine = integration_engine
    database.SessionLocal = sessionmaker(bind=integration_engine, autoflush=False, autocommit=False)

    try:
        if _dev_url and _same_target(str(database.engine.url), _dev_url):
            raise RuntimeError("Refusing to reset schema: database.engine is bound to the DEV database, not the disposable integration one.")

        database.Base.metadata.drop_all(bind=database.engine)
        database.Base.metadata.create_all(bind=database.engine)
        yield
    finally:
        integration_engine.dispose()
        database.engine = prior_engine
        database.SessionLocal = prior_session_local
        (
            celery_app.app.conf.broker_url,
            celery_app.app.conf.broker_connection_timeout,
            celery_app.app.conf.broker_transport_options,
        ) = prior_conf
        _reset_broker_connection_pools(celery_app.app)
        if prior_env is None:
            os.environ.pop("CELERY_BROKER_URL", None)
        else:
            os.environ["CELERY_BROKER_URL"] = prior_env
