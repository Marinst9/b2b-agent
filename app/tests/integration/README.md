# Real-broker integration tests

These tests require a REAL, reachable Redis, Postgres, and a REAL, separate
Linux Celery worker consuming from that Redis -- genuine network sockets,
genuine message serialization/delivery, genuine cross-process execution, no
eager mode, and never a worker running natively on the host. They run
against **disposable** infrastructure defined in
`../../../docker-compose.integration.yml`, entirely separate from your
development database and from `../../../docker-compose.yml` (the normal
background-processing setup): different container project name, different
host ports (Postgres `55432`, Redis `6380`), and a throwaway database with no
persistent volume.

**Safety guarantee**: `conftest.py` in this directory refuses to even start
(a hard `RuntimeError`, not a silent skip) if `INTEGRATION_DATABASE_URL`
resolves to the same host/port/database as your `.env`'s `DATABASE_URL`. This
protects your development data even if these env vars are ever
misconfigured, since every test here drops and recreates the schema.

## Architecture: a real, separate Linux worker container

`worker-integration` (see `../../../docker-compose.integration.yml`) is a
**real, required** part of the automated `pytest tests/integration` suite --
not an optional manual-verification extra. The suite's readiness check
(`conftest.py`'s `_worker_reachable()`) uses a Celery control-plane ping to
confirm an actual worker is consuming from the broker before running
anything, and every test in this directory is skipped (not errored) if it
isn't.

Because the worker runs in a genuinely separate container process,
`monkeypatch` in the pytest process cannot reach it. External providers
(website fetch, the LLM, email) are instead mocked **inside** that
container, via `container_fakes.py` + `container_entrypoint.py`, applied
before the real Celery worker starts consuming tasks (see
`worker-integration`'s `command:` in the compose file). The tests themselves
never call task/service functions directly -- they only create data via the
API and publish work (`tasks.dispatch_new_job` / `.delay()`, which just
serialize a message onto the real broker), then observe outcomes by polling
the shared disposable Postgres database.

`beat-integration` remains available for manually watching the periodic
outbox-sweep/lease-recovery tasks run against the disposable infra; it's not
exercised by the automated suite (beat only schedules tasks, it doesn't
execute provider calls itself).

## Status in this environment

**Verified: 4 passed, 0 failed, 0 skipped** (last run: 2026-09-15, twice in a
row for stability), against genuinely live infrastructure:

- `redis-integration`: real Redis 7, container port 6380.
- `postgres-integration`: real, disposable Postgres 16, container port 55432.
- `worker-integration`: a real `celery -A celery_app worker` process inside a
  Linux container (`Linux-6.18.33.2-microsoft-standard-WSL2`, confirmed via
  the worker's own startup banner), with providers mocked inside that same
  container process (never natively on the host, per this milestone's
  requirement).

Confirmed via the worker's own logs, not just test assertions: genuine
duplicate-delivery deduplication (`already claimed, completed, or terminal
(duplicate delivery)`) and a genuine Celery-scheduled retry (`retry: Retry in
1s: ConnectionError(...)`) both actually happening inside the real worker
process.

Two real bugs were caught and fixed only by actually running this for the
first time against live infrastructure (both previously invisible under
eager-mode/unit tests, which is exactly why this milestone treats real-broker
behavior as unverified until it's actually exercised):

1. **A Celery/environment-variable interaction**: the root `tests/conftest.py`
   sets `CELERY_BROKER_URL` in `os.environ` (to a deliberately-unreachable
   fake broker, for its own fast-failing unit tests). Once that env var is
   present, Celery resolves `app.conf.broker_url` from it *live* on every
   access -- plain attribute assignment (`app.conf.broker_url = X`) silently
   has no effect while the env var remains set. Fixed by overwriting the env
   var itself, not just the conf object (see `_apply_broker_override` in
   `conftest.py`).
2. **Cached connection pools**: `Celery.pool` and `AMQP.producer_pool` are
   memoized on first use and don't notice a later `broker_url` change --
   without discarding them, tests running after these ones (in a combined
   `pytest -q` run) silently kept publishing over the pooled connection to
   the *real* integration Redis instead of the intended fake/unreachable one.
   Fixed by explicitly dropping both caches whenever the broker override is
   applied or restored (`_reset_broker_connection_pools`).

Every override this suite applies (broker URL/timeouts, the ORM's
engine/session factory) is scoped to a single test via an autouse fixture and
restored immediately afterward, specifically so running
`pytest tests/integration` together with the rest of the suite (`pytest -q`
from `app/`) cannot leak state into unrelated tests -- verified directly by
running the full 239-test suite (`pytest -q`) twice in a row with no
failures.

The eager-mode tests (`../test_tasks_eager.py`), the pure-DB `job_service`
tests (`../test_job_service.py`), the draft-atomicity tests
(`../test_draft_atomicity.py`), and the provider-budget tests
(`../test_provider_budget.py`) remain the primary coverage for the
underlying logic (claiming, chaining, lease/version compare-and-swap,
cancellation, dispatch recovery, concurrency limiting) at the unit level;
these real-broker tests add exactly the guarantees eager mode structurally
cannot provide (real message delivery, real scheduled retries, real
duplicate delivery across a genuinely separate worker process).

## How to run these

```bash
# 1. from the repo root -- builds and starts the disposable test infra,
#    INCLUDING the real worker (required, not optional, for this suite)
docker compose -f docker-compose.integration.yml up -d --build redis-integration postgres-integration worker-integration

# 2. run the suite
cd app
../venv/Scripts/python.exe -m pytest tests/integration -v

# 3. tear down (discards all test data -- it was never persisted anyway)
cd ..
docker compose -f docker-compose.integration.yml down
```

To also manually observe a real containerized worker/beat processing jobs
created through the API:

```bash
docker compose -f docker-compose.integration.yml up -d --build
docker compose -f docker-compose.integration.yml logs -f worker-integration
# in another terminal, point the API at the same disposable DB/broker and
# create a job through it, e.g. via curl or the dashboard
```

If Redis, Postgres, or a live worker isn't reachable, every test in this
directory is skipped (not errored) with a message naming exactly what's
missing and the command above.
