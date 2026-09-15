"""Celery tasks for background research/qualification/draft generation.

Design rules enforced throughout (see the milestone requirements these map to):

* No database transaction is held open across a network/model call: each
  task claims its step in a short-lived DB session (commits immediately),
  closes it, does the network/LLM work with NO db session open, then opens a
  NEW short session to persist the result and commits. See `_claim`, and the
  `database.SessionLocal()` blocks in each task.

* Duplicate delivery safety comes entirely from job_service.claim_step's
  atomic compare-and-swap on the JobStep row -- a redelivered task that finds
  nothing left to claim just returns immediately (`_claim` returns None).
  IMPORTANT CAVEAT, stated explicitly rather than silently assumed away: if a
  worker crashes AFTER an external model/API call succeeded but BEFORE the
  DB write that records it, a redelivered or lease-recovered retry of that
  step WILL repeat the external call. Deduplication is guaranteed at the
  *persisted result* level (you will never get two DraftVersions or two
  research runs recorded for one step), not at the "did we already ask
  OpenAI" level -- there is no way to make an external, non-transactional API
  call exactly-once from a system that can crash between the call and the
  commit.

* Every write that finalizes a step's outcome (complete_step/fail_step/
  retry_step) is ALSO guarded by the caller's lease ownership, via the same
  compare-and-swap pattern (see job_service._write_terminal_step). This is
  what prevents a "zombie" worker -- one whose lease already expired and was
  reclaimed by a newer worker, but which is still alive and eventually
  finishes its (now-stale) work -- from overwriting whatever the current
  lease-holder wrote or is writing. See _finish_step below: a LeaseLostError
  from any of these calls is treated as a normal, silent no-op (the result
  is simply discarded), not a task failure.

* Celery-level retry (bounded exponential backoff + jitter, via
  `autoretry_for`/`retry_backoff`) is reserved for transient infrastructure
  failures ONLY -- and only for failures that occur OUTSIDE a layer that
  already retries internally. research_extractor.extract_facts and
  draft_generator.generate_draft already retry malformed-model-output
  failures internally (MAX_ATTEMPTS=3); their terminal ExtractionError /
  DraftGenerationError is deliberately NOT retried again at the task level,
  or attempts would multiply (3 internal x N task-level retries). See
  `_handle_task_exception` for exactly how a retryable failure is
  distinguished from a terminal one, and how the JobStep is put back to
  'queued' (not left 'running') before Celery actually retries the task --
  otherwise the retried attempt could never re-claim its own step.

* Workflow chaining (research -> qualify -> draft) is done by each step task
  dispatching the next one for the same lead when it finishes, rather than
  one long-running orchestrator task -- this is what makes "cancel between
  steps" and per-step timeouts work per-step instead of for an entire
  multi-lead campaign at once. A dispatch failure when publishing the NEXT
  step (e.g. the broker blips for a few seconds right after the current step
  finishes) must NOT be allowed to corrupt the current step's own already-
  successful outcome, and must NOT leave the next step stranded forever --
  see _dispatch_next_step_or_cancel's try/except and sweep_dispatch_outbox's
  "mid-chain" pass, which is what makes dispatch recovery cover every
  pending step, not only a job's initial publication.
"""
import logging

from sqlalchemy.exc import OperationalError

from celery_app import app, TASK_MAX_RETRIES, TASK_RETRY_BACKOFF, TASK_RETRY_BACKOFF_MAX
import database
import job_service as js
import research_service
import qualification_service
import draft_service
from modules import web_fetcher

logger = logging.getLogger(__name__)

# Transient, infra-level failures only -- see the module docstring for why
# ExtractionError/DraftGenerationError are deliberately excluded: they are
# already the terminal outcome of an internal bounded-retry loop.
RETRYABLE_EXCEPTIONS = (OperationalError, web_fetcher.FetchError, ConnectionError, TimeoutError)

_STEP_ORDER = ("research", "qualify", "draft")


def _next_step_type(step_type: str):
    idx = _STEP_ORDER.index(step_type)
    return _STEP_ORDER[idx + 1] if idx + 1 < len(_STEP_ORDER) else None


def _prev_step_type(step_type: str):
    idx = _STEP_ORDER.index(step_type)
    return _STEP_ORDER[idx - 1] if idx > 0 else None


def _worker_owner_id(task_id: str) -> str:
    return f"celery:{task_id}"


def _claim(step_id: int, task_id: str):
    """Opens a short-lived session, attempts the atomic claim, and always
    closes the session before returning -- no session stays open into the
    network/model call that follows."""
    db = database.SessionLocal()
    try:
        return js.claim_step(db, step_id, _worker_owner_id(task_id))
    finally:
        db.close()


def _finish_step(fn, *args, **kwargs):
    """Wraps complete_step/fail_step/retry_step: if the caller's lease was
    already reclaimed by a newer worker (LeaseLostError), this is a normal,
    silent no-op -- the caller's result is simply stale and must be
    discarded, exactly like a duplicate-delivery no-op. Returns the step, or
    None if the lease was lost."""
    db = database.SessionLocal()
    try:
        try:
            return fn(db, *args, **kwargs)
        except js.LeaseLostError as e:
            logger.info("discarding stale result: %s", e)
            return None
    finally:
        db.close()


def _handle_task_exception(celery_task, step_id: int, job_id: int, lead_id: int, step_type: str, lease_owner: str, exc: Exception):
    """Single place deciding retryable-vs-terminal, called from every task's
    except block. For a retryable, non-exhausted failure: releases the step
    back to 'queued' (so the *retried* task attempt can actually re-claim
    it -- marking it 'failed' here would strand it, since a terminal step
    can never be claimed again) and re-raises so Celery's autoretry_for
    performs the actual backoff+retry. For a terminal failure (retryable but
    exhausted, or never retryable to begin with): marks the step
    failed/needs_review, cancels the remaining steps for this lead in a
    workflow job, and returns a plain result (task ends cleanly, not as a
    Celery-level failure, since the DB is already the authoritative record).
    """
    is_retryable_type = isinstance(exc, RETRYABLE_EXCEPTIONS)
    is_final_attempt = celery_task.request.retries >= celery_task.max_retries

    if is_retryable_type and not is_final_attempt:
        step = _finish_step(js.retry_step, step_id, lease_owner)
        if step is None:
            return {"skipped": "lease already reclaimed by another worker"}
        raise exc  # let Celery's autoretry_for own the backoff+retry scheduling

    step = _finish_step(js.fail_step, step_id, lease_owner, exc, needs_review=True)
    if step is None:
        return {"skipped": "lease already reclaimed by another worker"}

    _cancel_remaining_steps_for_lead(job_id, lead_id, step_type, f"Skipped: '{step_type}' step failed ({js.sanitize_error(exc)}).")
    return {"failed": js.sanitize_error(exc), "retryable": is_retryable_type, "final_attempt": is_final_attempt}


def _dispatch_next_step_or_cancel(job_id: int, lead_id: int, current_step_type: str):
    """Called after a step SUCCEEDS. If this job is a workflow and there is
    a next step for this lead, dispatch it -- unless the job has been
    cancelled in the meantime (cooperative, "between steps" cancellation)."""
    next_type = _next_step_type(current_step_type)
    if next_type is None:
        return

    db = database.SessionLocal()
    try:
        job = db.get(database.Job, job_id)
        if job is None or job.job_type != "workflow":
            return
        next_step = (
            db.query(database.JobStep)
            .filter(database.JobStep.job_id == job_id, database.JobStep.lead_id == lead_id, database.JobStep.step_type == next_type)
            .first()
        )
        if next_step is None or next_step.status != "queued":
            return

        if job.status == "cancelled":
            js.cancel_step(db, next_step.id, "Job was cancelled before this step started.")
            return

        step_id = next_step.id
    finally:
        db.close()

    try:
        _dispatch_step_task(next_type, job_id, step_id)
    except Exception as e:
        # The CURRENT step already succeeded and must stay that way -- a
        # broker hiccup publishing the NEXT step must not be allowed to look
        # like the current one failed. The next step is simply left
        # 'queued' (it already is); sweep_dispatch_outbox's "mid-chain" pass
        # will find and redispatch it on its next run. This is exactly what
        # makes dispatch recovery cover every pending step, not only a job's
        # initial publication.
        logger.warning(
            "failed to publish next step (%s) for job=%s lead=%s: %s -- left queued for the periodic sweep",
            next_type, job_id, lead_id, js.sanitize_error(e),
        )


def _dispatch_step_task(step_type: str, job_id: int, step_id: int):
    task_by_type = {"research": run_research_step, "qualify": run_qualify_step, "draft": run_draft_step}
    task_by_type[step_type].delay(job_id, step_id)


def _cancel_remaining_steps_for_lead(job_id: int, lead_id: int, from_step_type: str, reason: str):
    next_type = _next_step_type(from_step_type)
    while next_type is not None:
        db = database.SessionLocal()
        try:
            step = (
                db.query(database.JobStep)
                .filter(database.JobStep.job_id == job_id, database.JobStep.lead_id == lead_id, database.JobStep.step_type == next_type)
                .first()
            )
            if step is not None and step.status == "queued":
                js.cancel_step(db, step.id, reason)
        finally:
            db.close()
        next_type = _next_step_type(next_type)


# --- research -----------------------------------------------------------------

@app.task(
    bind=True,
    autoretry_for=RETRYABLE_EXCEPTIONS,
    retry_backoff=TASK_RETRY_BACKOFF,
    retry_backoff_max=TASK_RETRY_BACKOFF_MAX,
    retry_jitter=True,
    max_retries=TASK_MAX_RETRIES,
)
def run_research_step(self, job_id: int, step_id: int):
    claimed = _claim(step_id, self.request.id)
    if claimed is None:
        return {"skipped": "already claimed, completed, or terminal (duplicate delivery)"}

    lead_id = claimed.lead_id
    lease_owner = claimed.lease_owner

    try:
        db = database.SessionLocal()
        try:
            research = research_service.run_research(db, lead_id)
            summary = {"status": research.status, "pages_fetched": research.pages_fetched, "version": research.version}
        finally:
            db.close()
    except Exception as e:
        return _handle_task_exception(self, step_id, job_id, lead_id, "research", lease_owner, e)

    if _finish_step(js.complete_step, step_id, lease_owner, summary) is None:
        return {"skipped": "lease already reclaimed by another worker"}

    _dispatch_next_step_or_cancel(job_id, lead_id, "research")
    return summary


# --- qualify --------------------------------------------------------------------

@app.task(
    bind=True,
    autoretry_for=RETRYABLE_EXCEPTIONS,
    retry_backoff=TASK_RETRY_BACKOFF,
    retry_backoff_max=TASK_RETRY_BACKOFF_MAX,
    retry_jitter=True,
    max_retries=TASK_MAX_RETRIES,
)
def run_qualify_step(self, job_id: int, step_id: int):
    claimed = _claim(step_id, self.request.id)
    if claimed is None:
        return {"skipped": "already claimed, completed, or terminal (duplicate delivery)"}

    lead_id = claimed.lead_id
    lease_owner = claimed.lease_owner

    try:
        db = database.SessionLocal()
        try:
            qualification = qualification_service.qualify_lead(db, lead_id)
            summary = {"fit_score": qualification.fit_score, "evidence_coverage": qualification.evidence_coverage}
        finally:
            db.close()
    except Exception as e:
        return _handle_task_exception(self, step_id, job_id, lead_id, "qualify", lease_owner, e)

    if _finish_step(js.complete_step, step_id, lease_owner, summary) is None:
        return {"skipped": "lease already reclaimed by another worker"}

    _dispatch_next_step_or_cancel(job_id, lead_id, "qualify")
    return summary


# --- draft ----------------------------------------------------------------------

@app.task(
    bind=True,
    autoretry_for=RETRYABLE_EXCEPTIONS,
    retry_backoff=TASK_RETRY_BACKOFF,
    retry_backoff_max=TASK_RETRY_BACKOFF_MAX,
    retry_jitter=True,
    max_retries=TASK_MAX_RETRIES,
)
def run_draft_step(self, job_id: int, step_id: int):
    claimed = _claim(step_id, self.request.id)
    if claimed is None:
        return {"skipped": "already claimed, completed, or terminal (duplicate delivery)"}

    lead_id = claimed.lead_id
    lease_owner = claimed.lease_owner

    db = database.SessionLocal()
    try:
        job = db.get(database.Job, job_id)
        params = job.params or {}
        # Captured now (right as work begins), not at job-creation time, to
        # keep the window in which a manual edit could race us as small as
        # possible; this is the version-safety baseline re-checked ATOMICALLY
        # right before the result is persisted (see
        # draft_service.generate_draft_if_still_applicable, which performs
        # the check and the write as a single compare-and-swap UPDATE, not a
        # separate read-then-write with a race window in between).
        expected_base_version = draft_service.current_draft_version(db, lead_id)
    finally:
        db.close()

    try:
        db = database.SessionLocal()
        try:
            result = draft_service.generate_draft_if_still_applicable(
                db,
                lead_id,
                expected_base_version,
                language=params.get("language", draft_service.DEFAULT_LANGUAGE),
                tone=params.get("tone", draft_service.DEFAULT_TONE),
                length=params.get("length", draft_service.DEFAULT_LENGTH),
            )
        finally:
            db.close()
    except Exception as e:
        return _handle_task_exception(self, step_id, job_id, lead_id, "draft", lease_owner, e)

    if result["applied"]:
        draft = result["draft"]
        outcome = {"applied": True, "draft_version": draft.version}
    else:
        # Superseded by a newer manual edit/regeneration that landed while
        # this task was running. Nothing is overwritten; the generated
        # content is preserved so the user can see it and explicitly choose
        # to rerun instead of losing it silently.
        outcome = {
            "applied": False,
            "superseded": True,
            "expected_base_version": expected_base_version,
            "current_version": result["current_version"],
            "generated_content": result["content"],
        }

    if _finish_step(js.complete_step, step_id, lease_owner, outcome) is None:
        return {"skipped": "lease already reclaimed by another worker"}

    return result


# --- periodic maintenance tasks --------------------------------------------------

@app.task
def sweep_dispatch_outbox():
    """Periodic durable-dispatch sweep. Covers BOTH:

    (a) a job whose INITIAL publish never succeeded (job.dispatched=False)
    (b) a job that WAS successfully dispatched at least once but has a
        LATER step that is 'queued' and ready-to-run yet was never actually
        published -- e.g. the mid-workflow chain dispatch fired from a
        completing step's own task (_dispatch_next_step_or_cancel) failed
        because the broker blipped for a few seconds right at that moment.
        (a) alone would never catch this, since the job-level `dispatched`
        flag only ever reflects the FIRST publish attempt.

    Re-publishing is always safe to repeat: job_service.claim_step's
    compare-and-swap makes a duplicate/redundant dispatch of an
    already-claimed or already-finished step a harmless no-op.
    """
    db = database.SessionLocal()
    try:
        newly_dispatched = js.sweep_undispatched_jobs(db, publish_fn=_publish_job)

        mid_chain_ids = []
        active_jobs = db.query(database.Job).filter(database.Job.status.in_(["queued", "running"])).all()
        for job in active_jobs:
            if job.id in newly_dispatched:
                continue  # already just (re)published above
            steps_by_lead_and_type = {(s.lead_id, s.step_type): s for s in job.steps}
            if any(_is_ready_to_dispatch(job, s, steps_by_lead_and_type) for s in job.steps):
                mid_chain_ids.append(job.id)
    finally:
        db.close()

    for job_id in mid_chain_ids:
        _publish_job(job_id)

    total = len(newly_dispatched) + len(mid_chain_ids)
    if total:
        logger.info(
            "sweep_dispatch_outbox: dispatched %d job(s) with pending steps (%d new, %d mid-chain)",
            total, len(newly_dispatched), len(mid_chain_ids),
        )
    return {"newly_dispatched": newly_dispatched, "mid_chain_redispatched": mid_chain_ids}


@app.task
def recover_abandoned_steps_task():
    """Periodic lease-recovery sweep: requeues steps whose owning worker
    crashed (or was killed) without releasing its lease."""
    db = database.SessionLocal()
    try:
        recovered = js.recover_abandoned_steps(db)
        step_ids = [(s.id, s.job_id, s.step_type) for s in recovered]
    finally:
        db.close()

    for step_id, job_id, step_type in step_ids:
        _dispatch_step_task(step_type, job_id, step_id)

    if step_ids:
        logger.warning("recover_abandoned_steps_task: requeued %d abandoned step(s)", len(step_ids))
    return len(step_ids)


def _is_ready_to_dispatch(job, step, steps_by_lead_and_type: dict) -> bool:
    """A step is ready right now if it's 'queued' AND (this isn't a workflow
    job, OR it's the first step type, OR its predecessor for the SAME lead
    has already succeeded). This one rule correctly covers a brand-new
    workflow job (only each lead's research step is ready; qualify/draft are
    also 'queued' at creation time but must wait), a RESUMED job (e.g.
    research already succeeded, only qualify was reset to 'queued' -- it is
    now immediately ready), AND a mid-chain dispatch failure (research
    succeeded, but publishing qualify failed -- it stays 'queued' and ready,
    exactly like the resumed case)."""
    if step.status != "queued":
        return False
    if job.job_type != "workflow":
        return True
    prev_type = _prev_step_type(step.step_type)
    if prev_type is None:
        return True
    prev_step = steps_by_lead_and_type.get((step.lead_id, prev_type))
    return prev_step is not None and prev_step.status == "succeeded"


def _publish_job(job_id: int) -> str:
    """Publishes every step currently ready to run for this job (see
    _is_ready_to_dispatch). Used for a job's initial dispatch, for resuming
    one after job_service.resume_job has reset some of its steps back to
    'queued', and for the sweep's mid-chain recovery pass -- the same
    readiness rule handles all three correctly."""
    db = database.SessionLocal()
    try:
        job = db.get(database.Job, job_id)
        if job is None:
            raise js.JobServiceError(f"Job {job_id} not found.")
        all_steps = job.steps
        steps_by_lead_and_type = {(s.lead_id, s.step_type): s for s in all_steps}
        ready = [(s.step_type, s.id) for s in all_steps if _is_ready_to_dispatch(job, s, steps_by_lead_and_type)]
    finally:
        db.close()

    for step_type, step_id in ready:
        _dispatch_step_task(step_type, job_id, step_id)
    return f"dispatched:{len(ready)}"


def dispatch_new_job(db, job_id: int) -> bool:
    """Entry point used by api.py right after create_job() AND after
    job_service.resume_job(): tries to publish immediately; on broker
    failure the job is simply left for the outbox sweep, per the
    durable-dispatch requirement."""
    return js.dispatch_job(db, job_id, publish_fn=_publish_job)
