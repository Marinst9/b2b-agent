"""Celery application for background research/qualification/draft processing.

PostgreSQL (via job_service.Job/JobStep) is the authoritative status store --
this Celery app and its Redis broker are only the delivery mechanism. If
Redis is down, jobs still get created (see job_service.create_job) and just
sit undispatched until the outbox sweep can publish them (see
job_service.sweep_undispatched_jobs and the periodic task below).

All settings are configurable via environment variables so the same code
runs against a local dev Redis or a container one (see docker-compose.yml).
"""
import os

from celery import Celery
from dotenv import load_dotenv

load_dotenv()

BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")

# Bounded exponential backoff with jitter for CELERY-LEVEL retries only.
# These apply to transient infrastructure failures (DB connection blips,
# network errors reaching a provider) -- NOT to errors that the service
# layer's own bounded retry already exhausted (see tasks.py's
# RETRYABLE_EXCEPTIONS / _is_task_level_retryable for the exact split, which
# is what keeps retries from multiplying across layers).
TASK_MAX_RETRIES = int(os.getenv("CELERY_TASK_MAX_RETRIES", "3"))
TASK_RETRY_BACKOFF = int(os.getenv("CELERY_TASK_RETRY_BACKOFF_SECONDS", "5"))
TASK_RETRY_BACKOFF_MAX = int(os.getenv("CELERY_TASK_RETRY_BACKOFF_MAX_SECONDS", "120"))

# Per-task wall-clock budgets. Soft limit raises a catchable exception inside
# the task (so we can still mark the step failed cleanly); hard limit kills
# the worker process for that task if the soft limit is somehow not honored.
TASK_SOFT_TIME_LIMIT = int(os.getenv("CELERY_TASK_SOFT_TIME_LIMIT_SECONDS", "90"))
TASK_TIME_LIMIT = int(os.getenv("CELERY_TASK_TIME_LIMIT_SECONDS", "120"))

# Worker concurrency (how many tasks run in parallel per worker process) is a
# CLI flag (`celery -A celery_app worker --concurrency=N`), documented in
# docker-compose.yml, not a code setting -- Celery does not expose it here.

app = Celery("b2b_agent", broker=BROKER_URL, include=["tasks"])

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # PostgreSQL (job_service.Job/JobStep) is the authoritative status store
    # for everything a caller needs to know about a task's outcome -- we
    # never call .get()/AsyncResult on a dispatched task in production code,
    # so no result backend is configured at all. This also sidesteps a real
    # footgun: a misbehaving/unreachable result backend has its OWN,
    # separate reconnect-retry policy from the broker, and it was observed
    # here to retry for 100+ seconds if left configured -- exactly the kind
    # of hang a fail-fast dispatch design must not have. Tests calling
    # `.apply(...).get()` in eager mode are unaffected: eager results are
    # produced and read in-process, without touching any backend.
    task_ignore_result=True,
    # Redelivers a task if the worker dies before acking it -- safe here
    # because every task claims its JobStep via job_service.claim_step's
    # atomic compare-and-swap before doing any work, so a redelivered task
    # either finds nothing left to claim (no-op) or recovers an abandoned one.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=TASK_SOFT_TIME_LIMIT,
    task_time_limit=TASK_TIME_LIMIT,
    broker_connection_retry_on_startup=True,
    task_default_queue="b2b_agent",
    # Producer-side (API process) publish must fail FAST, not hang or retry
    # internally: job_service's outbox sweep is already the retry mechanism
    # for a down broker, and retrying here too would multiply retries across
    # layers exactly as the durable-dispatch design is meant to avoid. A
    # short connect timeout also keeps a broker outage from ever turning an
    # API request into a multi-second (or longer) hang.
    broker_connection_timeout=float(os.getenv("CELERY_BROKER_CONNECT_TIMEOUT_SECONDS", "2")),
    broker_transport_options={
        "socket_connect_timeout": float(os.getenv("CELERY_BROKER_CONNECT_TIMEOUT_SECONDS", "2")),
        "socket_timeout": float(os.getenv("CELERY_BROKER_CONNECT_TIMEOUT_SECONDS", "2")),
    },
    task_publish_retry=False,
    beat_schedule={
        "sweep-dispatch-outbox": {
            "task": "tasks.sweep_dispatch_outbox",
            "schedule": float(os.getenv("OUTBOX_SWEEP_INTERVAL_SECONDS", "15")),
        },
        "recover-abandoned-steps": {
            "task": "tasks.recover_abandoned_steps_task",
            "schedule": float(os.getenv("LEASE_RECOVERY_INTERVAL_SECONDS", "60")),
        },
    },
)
