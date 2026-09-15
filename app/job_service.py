"""Persistent job/step tracking for background processing.

PostgreSQL (via the Job/JobStep tables) is the authoritative status store.
Celery + Redis are only the delivery mechanism -- every state transition here
is a plain database write, and a step's identity (job_id, lead_id, step_type)
is unique (see database.JobStep.__table_args__), so it is always safe to ask
"has this already been done?" before doing it again. This is what makes
duplicate task delivery and worker crashes safe to handle (see claim_step).

Durable dispatch (the "outbox" pattern): create_job() commits a queued,
undispatched Job row FIRST, inside its own transaction. dispatch_job() then
tries to publish it to Celery; if the broker is unreachable, the row is
simply left `dispatched=False` and sweep_undispatched_jobs() (called
opportunistically from the API, and by the periodic `sweep_dispatch_outbox`
Celery task once a worker is up) will retry publishing it later. A job can
never be silently lost between the DB commit and the queue publish -- worst
case, it sits queued-but-undispatched until the next sweep, which is exactly
the durability guarantee this pattern is for.
"""
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import update, and_
from sqlalchemy.orm import Session

from database import Job, JobStep, Campaign

STEP_TYPES = ("research", "qualify", "draft")
JOB_TYPES = ("research", "qualify", "draft", "workflow")
STATUSES = ("queued", "running", "succeeded", "failed", "cancelled", "needs_review")
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled", "needs_review")

DEFAULT_LEASE_SECONDS = 15 * 60  # generous vs. worst-case task duration (research: up to ~5 pages x 8s timeout)
DEFAULT_MAX_ATTEMPTS = 3

# Best-effort secret redaction for anything persisted to `last_error`. Not a
# substitute for not logging secrets in the first place, but a safety net.
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{10,}"),  # OpenAI-style API keys
    re.compile(r"(?i)(api[_-]?key|authorization|password|secret|token)\s*[:=]\s*\S+"),
    re.compile(r"postgres(?:ql)?://\S+"),  # DB connection strings (may embed credentials)
]


class JobServiceError(Exception):
    """Raised for request-level errors that should map to a 4xx HTTP response."""


def sanitize_error(value) -> str:
    """Best-effort redaction of anything that looks like a secret, plus a
    length cap so a runaway traceback/error string can't bloat storage."""
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    max_length = 500
    if len(text) > max_length:
        text = text[:max_length] + "...[truncated]"
    return text


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _new_key() -> str:
    return secrets.token_hex(16)


# --- job/step creation -----------------------------------------------------

def create_job(db: Session, campaign_id: int, job_type: str, lead_ids: list, params: Optional[dict] = None) -> Job:
    if job_type not in JOB_TYPES:
        raise JobServiceError(f"job_type must be one of {JOB_TYPES}, got '{job_type}'.")
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise JobServiceError(f"Campaign {campaign_id} not found.")
    if not lead_ids:
        raise JobServiceError("At least one lead is required to create a job.")

    job = Job(
        campaign_id=campaign_id,
        job_type=job_type,
        status="queued",
        params=params or {},
        dispatched=False,
        idempotency_key=_new_key(),
    )
    db.add(job)
    db.flush()  # assigns job.id without committing yet

    step_types = STEP_TYPES if job_type == "workflow" else (job_type,)
    for lead_id in lead_ids:
        for step_type in step_types:
            db.add(
                JobStep(
                    job_id=job.id,
                    lead_id=lead_id,
                    step_type=step_type,
                    status="queued",
                    max_attempts=DEFAULT_MAX_ATTEMPTS,
                    input_version_snapshot={},
                    idempotency_key=_new_key(),
                )
            )

    db.commit()
    db.refresh(job)
    return job


def get_job_or_404(db: Session, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise JobServiceError(f"Job {job_id} not found.")
    return job


def list_jobs_for_campaign(db: Session, campaign_id: int) -> list:
    return db.query(Job).filter(Job.campaign_id == campaign_id).order_by(Job.id.desc()).all()


# --- durable dispatch (outbox) ----------------------------------------------

def mark_dispatched(db: Session, job_id: int, celery_task_id: str):
    job = db.get(Job, job_id)
    job.dispatched = True
    job.celery_task_id = celery_task_id
    db.commit()


def record_dispatch_failure(db: Session, job_id: int):
    job = db.get(Job, job_id)
    job.dispatch_attempts += 1
    db.commit()


def dispatch_job(db: Session, job_id: int, publish_fn) -> bool:
    """Attempts to publish `job_id` to the broker via `publish_fn(job_id)`
    (expected to return a task id string, or raise on broker failure).
    Returns True if dispatched. Never raises -- a broker outage here just
    means the job stays undispatched for the next sweep."""
    try:
        task_id = publish_fn(job_id)
    except Exception:
        record_dispatch_failure(db, job_id)
        return False
    mark_dispatched(db, job_id, task_id)
    return True


def sweep_undispatched_jobs(db: Session, publish_fn) -> list:
    """The outbox sweep: (re)tries to dispatch every Job that isn't marked
    dispatched yet, including ones a prior dispatch attempt already failed
    for. Returns the ids of jobs newly dispatched by this call."""
    pending = (
        db.query(Job)
        .filter(Job.dispatched.is_(False), Job.status.notin_(["cancelled"]))
        .order_by(Job.id.asc())
        .all()
    )
    dispatched_ids = []
    for job in pending:
        if dispatch_job(db, job.id, publish_fn):
            dispatched_ids.append(job.id)
    return dispatched_ids


# --- step claiming (duplicate-delivery- and crash-safe) ---------------------

def claim_step(db: Session, step_id: int, lease_owner: str, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> Optional[JobStep]:
    """Atomic compare-and-swap claim. Succeeds only if the step is currently
    'queued' (a fresh dispatch) or 'running' with an EXPIRED lease (an
    abandoned/crashed worker being recovered). Returns the claimed JobStep,
    or None if some other worker already holds or already finished it --
    which is exactly how duplicate task delivery becomes a safe no-op instead
    of duplicated work.
    """
    now = datetime.now(timezone.utc)
    step = db.get(JobStep, step_id)
    if step is None:
        return None

    if step.status == "queued":
        where_clause = and_(JobStep.id == step_id, JobStep.status == "queued")
    elif step.status == "running":
        expires_at = _aware(step.lease_expires_at)
        if expires_at is None or expires_at >= now:
            return None  # still validly leased by someone else -- not ours to take
        # Optimistic lock on the previous lease_owner so two concurrent
        # recovery attempts on the same expired lease can't both "win".
        where_clause = and_(JobStep.id == step_id, JobStep.status == "running", JobStep.lease_owner == step.lease_owner)
    else:
        return None  # already in a terminal state

    stmt = (
        update(JobStep)
        .where(where_clause)
        .values(
            status="running",
            lease_owner=lease_owner,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            attempt_count=JobStep.attempt_count + 1,
            started_at=step.started_at or now,
        )
    )
    result = db.execute(stmt)
    db.commit()
    if result.rowcount != 1:
        return None
    db.refresh(step)
    return step


def release_step_lease(db: Session, step_id: int):
    """Releases a claimed step back to 'queued' without recording success or
    failure -- used when a task can't proceed for a reason that isn't the
    step's own fault (e.g. cooperative cancellation observed mid-workflow)."""
    step = db.get(JobStep, step_id)
    if step is None:
        return
    step.status = "queued"
    step.lease_owner = None
    step.lease_expires_at = None
    db.commit()


class LeaseLostError(Exception):
    """Raised by complete_step/fail_step when the caller no longer holds this
    step's lease -- another worker already reclaimed it (e.g. this worker's
    lease expired and was recovered) after this caller last checked. Callers
    must treat this exactly like a duplicate-delivery no-op: their in-memory
    result is simply discarded, never force-written."""


def _write_terminal_step(db: Session, step_id: int, lease_owner: str, values: dict) -> Optional[JobStep]:
    """Single place implementing the lease-owner-guarded compare-and-swap
    that EVERY step-completion write goes through. Mirrors claim_step's own
    CAS: the UPDATE's WHERE clause requires lease_owner to still match the
    caller's, so a "zombie" worker -- one whose lease already expired and was
    reclaimed by a newer worker, but which is still alive and eventually
    finishes its (possibly stale) work -- can never overwrite what the
    current lease-holder wrote or is writing. Without this guard, a step's
    completion functions would trust the caller's word for which step it is
    without checking the caller is still the legitimate owner, which is
    exactly the gap that would let a late/zombie write win a race it should
    have lost.
    """
    stmt = update(JobStep).where(JobStep.id == step_id, JobStep.lease_owner == lease_owner).values(**values)
    result = db.execute(stmt)
    db.commit()
    if result.rowcount != 1:
        return None
    return db.get(JobStep, step_id)


def complete_step(db: Session, step_id: int, lease_owner: str, result_summary: Optional[dict] = None) -> JobStep:
    step = _write_terminal_step(
        db,
        step_id,
        lease_owner,
        {"status": "succeeded", "result_summary": result_summary or {}, "finished_at": datetime.now(timezone.utc)},
    )
    if step is None:
        raise LeaseLostError(f"step {step_id}: lease no longer held by {lease_owner!r}; result discarded")
    _maybe_finalize_job(db, step.job_id)
    db.refresh(step)
    return step


def fail_step(db: Session, step_id: int, lease_owner: str, error, *, needs_review: bool = False) -> JobStep:
    step = _write_terminal_step(
        db,
        step_id,
        lease_owner,
        {
            "status": "needs_review" if needs_review else "failed",
            "last_error": sanitize_error(error),
            "finished_at": datetime.now(timezone.utc),
        },
    )
    if step is None:
        raise LeaseLostError(f"step {step_id}: lease no longer held by {lease_owner!r}; failure discarded")
    _maybe_finalize_job(db, step.job_id)
    db.refresh(step)
    return step


def retry_step(db: Session, step_id: int, lease_owner: str) -> JobStep:
    """Releases the step back to 'queued' for another attempt (used by the
    Celery-level bounded-retry path for transient failures) without touching
    attempt_count (claim_step increments that on the NEXT claim). Also
    lease-owner-guarded: if this caller's lease was already reclaimed by a
    newer worker, that worker's claim must not be knocked back to 'queued'
    out from under it."""
    step = _write_terminal_step_nonterminal(db, step_id, lease_owner, {"status": "queued", "lease_owner": None, "lease_expires_at": None})
    if step is None:
        raise LeaseLostError(f"step {step_id}: lease no longer held by {lease_owner!r}; retry-release skipped")
    db.refresh(step)
    return step


def _write_terminal_step_nonterminal(db: Session, step_id: int, lease_owner: str, values: dict) -> Optional[JobStep]:
    """Same lease-owner CAS as _write_terminal_step, for writes that don't go
    through _maybe_finalize_job (the step isn't reaching a terminal status)."""
    stmt = update(JobStep).where(JobStep.id == step_id, JobStep.lease_owner == lease_owner).values(**values)
    result = db.execute(stmt)
    db.commit()
    if result.rowcount != 1:
        return None
    return db.get(JobStep, step_id)


def cancel_step(db: Session, step_id: int, reason: str) -> JobStep:
    """Unlike complete_step/fail_step, cancellation is not lease-guarded: it
    is used to cancel a step that is still 'queued' (never claimed at all --
    see cancel_job and the workflow "stop downstream steps" path), so there
    is no lease to protect against a zombie writer racing it."""
    step = db.get(JobStep, step_id)
    step.status = "cancelled"
    step.result_summary = {"reason": reason}
    step.finished_at = datetime.now(timezone.utc)
    db.commit()
    _maybe_finalize_job(db, step.job_id)
    db.refresh(step)
    return step


def _maybe_finalize_job(db: Session, job_id: int):
    job = db.get(Job, job_id)
    steps = job.steps
    if not steps:
        return
    if not all(s.status in TERMINAL_STATUSES for s in steps):
        if job.status == "queued":
            job.status = "running"
            job.started_at = job.started_at or datetime.now(timezone.utc)
            db.commit()
        return

    if all(s.status == "cancelled" for s in steps):
        job.status = "cancelled"
    elif any(s.status == "needs_review" for s in steps):
        job.status = "needs_review"
    elif any(s.status == "failed" for s in steps):
        job.status = "failed"
    else:
        job.status = "succeeded"
    job.finished_at = datetime.now(timezone.utc)
    db.commit()


# --- user-triggered cancel / resume ------------------------------------------

def cancel_job(db: Session, job_id: int) -> Job:
    """Cooperative cancellation: any step still 'queued' is cancelled
    immediately. A step already 'running' is left alone -- the task checks
    job.status at its next between-steps checkpoint (see tasks.py) and stops
    itself there, which is the "support cancellation between steps"
    guarantee; a single step's own network/model call is not interrupted
    mid-flight."""
    job = get_job_or_404(db, job_id)
    if job.status in TERMINAL_STATUSES:
        raise JobServiceError(f"Job {job_id} is already '{job.status}'; nothing to cancel.")

    job.status = "cancelled"
    for step in job.steps:
        if step.status == "queued":
            step.status = "cancelled"
            step.result_summary = {"reason": "Job was cancelled before this step started."}
            step.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return job


def resume_job(db: Session, job_id: int) -> list:
    """Re-queues only this job's failed/needs_review/cancelled steps for
    another attempt -- 'succeeded' steps are left completely untouched, so
    resuming never re-runs completed work or recreates an existing
    DraftVersion. Returns the steps that were reset to 'queued' (callers
    should dispatch each one)."""
    job = get_job_or_404(db, job_id)
    if job.status not in ("failed", "needs_review", "cancelled"):
        raise JobServiceError(f"Job {job_id} is '{job.status}'; only failed/needs_review/cancelled jobs can be resumed.")

    to_redispatch = []
    for step in job.steps:
        if step.status in ("failed", "needs_review", "cancelled"):
            step.status = "queued"
            step.lease_owner = None
            step.lease_expires_at = None
            step.last_error = None
            step.finished_at = None
            to_redispatch.append(step)

    job.status = "queued"
    job.finished_at = None
    job.dispatched = False  # goes through the normal outbox dispatch path again
    db.commit()
    return to_redispatch


def recover_abandoned_steps(db: Session) -> list:
    """Finds 'running' steps whose lease has expired -- the owning worker
    presumably crashed or was killed without releasing it -- and resets them
    to 'queued' for redispatch, or to 'needs_review' if max_attempts is
    already exhausted. Returns the steps reset to 'queued' (callers should
    redispatch each one). Intended to run periodically (see the
    `recover_abandoned_steps` Celery beat task)."""
    now = datetime.now(timezone.utc)
    recovered = []
    for step in db.query(JobStep).filter(JobStep.status == "running").all():
        expires_at = _aware(step.lease_expires_at)
        if expires_at is None or expires_at >= now:
            continue
        if step.attempt_count >= step.max_attempts:
            step.status = "needs_review"
            step.last_error = "Exceeded max attempts after a worker lease expired (the worker likely crashed)."
            step.finished_at = now
            _maybe_finalize_job(db, step.job_id)
        else:
            step.status = "queued"
            step.lease_owner = None
            step.lease_expires_at = None
            recovered.append(step)
    db.commit()
    return recovered
