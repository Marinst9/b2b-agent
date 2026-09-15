from datetime import datetime, timedelta, timezone

import pytest

import job_service as js
from database import Job, JobStep


def create_campaign(client, name="Jobs Co"):
    return client.post("/campaigns", json={"name": name}).json()["id"]


def import_leads(client, campaign_id, n=2):
    rows = [f"L{i},Co{i},IT,Macedonia,l{i}@co.example," for i in range(n)]
    csv_bytes = ("name,company,industry,country,email,website\n" + "\n".join(rows)).encode()
    client.post(f"/campaigns/{campaign_id}/leads/import", files={"file": ("leads.csv", csv_bytes, "text/csv")})
    return [l["id"] for l in client.get(f"/campaigns/{campaign_id}/leads").json()]


def get_db():
    import database

    return database.SessionLocal()


# --- job/step creation -----------------------------------------------------

def test_create_job_creates_one_step_per_lead_for_single_step_job(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 2)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        assert job.status == "queued"
        assert job.dispatched is False
        assert len(job.steps) == 2
        assert {s.step_type for s in job.steps} == {"research"}
        assert all(s.status == "queued" for s in job.steps)
    finally:
        db.close()


def test_create_workflow_job_creates_three_steps_per_lead(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 1)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "workflow", lead_ids)
        assert len(job.steps) == 3
        assert {s.step_type for s in job.steps} == {"research", "qualify", "draft"}
    finally:
        db.close()


def test_create_job_rejects_unknown_job_type(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 1)
    db = get_db()
    try:
        with pytest.raises(js.JobServiceError):
            js.create_job(db, campaign_id, "not-a-real-type", lead_ids)
    finally:
        db.close()


def test_job_and_step_idempotency_keys_are_unique(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 2)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        keys = [s.idempotency_key for s in job.steps]
        assert len(keys) == len(set(keys))
    finally:
        db.close()


# --- durable dispatch / outbox ----------------------------------------------

def test_dispatch_job_marks_dispatched_on_success(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 1)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        ok = js.dispatch_job(db, job.id, publish_fn=lambda job_id: "fake-task-id")
        assert ok is True
        db.refresh(job)
        assert job.dispatched is True
        assert job.celery_task_id == "fake-task-id"
    finally:
        db.close()


def test_dispatch_job_survives_broker_outage_without_losing_the_job(client):
    """The core outbox guarantee: a broker publish failure must never lose
    the job -- it just stays undispatched for the next sweep."""
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 1)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)

        def broken_publish(job_id):
            raise ConnectionError("broker unreachable")

        ok = js.dispatch_job(db, job.id, publish_fn=broken_publish)
        assert ok is False

        # The job row still exists, unharmed, just not dispatched yet.
        reloaded = db.get(Job, job.id)
        assert reloaded is not None
        assert reloaded.dispatched is False
        assert reloaded.dispatch_attempts == 1
        assert reloaded.status == "queued"
    finally:
        db.close()


def test_sweep_undispatched_jobs_retries_and_recovers_after_broker_comes_back(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 1)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        js.dispatch_job(db, job.id, publish_fn=lambda job_id: (_ for _ in ()).throw(ConnectionError("down")))

        dispatched = js.sweep_undispatched_jobs(db, publish_fn=lambda job_id: (_ for _ in ()).throw(ConnectionError("still down")))
        assert dispatched == []

        dispatched = js.sweep_undispatched_jobs(db, publish_fn=lambda job_id: "recovered-task-id")
        assert dispatched == [job.id]
        db.refresh(job)
        assert job.dispatched is True
    finally:
        db.close()


def test_sweep_does_not_redispatch_cancelled_jobs(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 1)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        js.cancel_job(db, job.id)

        calls = []
        dispatched = js.sweep_undispatched_jobs(db, publish_fn=lambda job_id: calls.append(job_id) or "x")
        assert dispatched == []
        assert calls == []
    finally:
        db.close()


# --- claim_step: duplicate delivery and crash safety ------------------------

def test_claim_step_succeeds_once_for_a_queued_step(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 1)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        step_id = job.steps[0].id

        claimed = js.claim_step(db, step_id, lease_owner="worker-1")
        assert claimed is not None
        assert claimed.status == "running"
        assert claimed.attempt_count == 1
    finally:
        db.close()


def test_duplicate_task_delivery_second_claim_is_a_safe_no_op(client):
    """Simulates Celery's at-least-once delivery: the same task message
    processed twice must not duplicate work -- the second claim must fail."""
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 1)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        step_id = job.steps[0].id

        first = js.claim_step(db, step_id, lease_owner="worker-1")
        second = js.claim_step(db, step_id, lease_owner="worker-2")  # redelivered, maybe to a different worker

        assert first is not None
        assert second is None  # the duplicate delivery gets nothing to do
    finally:
        db.close()


def test_claim_step_fails_for_already_succeeded_step(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 1)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        step_id = job.steps[0].id
        js.claim_step(db, step_id, lease_owner="worker-1")
        js.complete_step(db, step_id, "worker-1", {"ok": True})

        again = js.claim_step(db, step_id, lease_owner="worker-2")
        assert again is None
    finally:
        db.close()


def test_claim_step_recovers_an_expired_lease_from_a_crashed_worker(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 1)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        step_id = job.steps[0].id
        js.claim_step(db, step_id, lease_owner="worker-1", lease_seconds=1)

        # Simulate the lease having already expired (worker crashed mid-task).
        step = db.get(JobStep, step_id)
        step.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

        recovered_claim = js.claim_step(db, step_id, lease_owner="worker-2")
        assert recovered_claim is not None
        assert recovered_claim.lease_owner == "worker-2"
        assert recovered_claim.attempt_count == 2  # second attempt
    finally:
        db.close()


def test_claim_step_does_not_recover_a_still_valid_lease(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 1)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        step_id = job.steps[0].id
        js.claim_step(db, step_id, lease_owner="worker-1", lease_seconds=600)

        stolen = js.claim_step(db, step_id, lease_owner="worker-2")
        assert stolen is None
    finally:
        db.close()


# --- step outcomes and job finalization -------------------------------------

def test_job_succeeds_when_all_steps_succeed(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 2)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        for step in job.steps:
            js.claim_step(db, step.id, "worker-1")
            js.complete_step(db, step.id, "worker-1", {"ok": True})

        db.refresh(job)
        assert job.status == "succeeded"
        assert job.finished_at is not None
    finally:
        db.close()


def test_job_is_needs_review_if_any_step_needs_review(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 2)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        js.claim_step(db, job.steps[0].id, "worker-1")
        js.complete_step(db, job.steps[0].id, "worker-1", {"ok": True})
        js.claim_step(db, job.steps[1].id, "worker-1")
        js.fail_step(db, job.steps[1].id, "worker-1", "boom", needs_review=True)

        db.refresh(job)
        assert job.status == "needs_review"
    finally:
        db.close()


def test_job_is_running_while_steps_are_still_in_progress(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 2)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        js.claim_step(db, job.steps[0].id, "worker-1")
        js.complete_step(db, job.steps[0].id, "worker-1", {"ok": True})

        db.refresh(job)
        assert job.status == "running"  # one step still queued
    finally:
        db.close()


# --- error sanitization -----------------------------------------------------

def test_sanitize_error_redacts_openai_style_keys():
    text = js.sanitize_error(Exception("call failed with key sk-abcdefghijklmnop1234567890"))
    assert "sk-abcdefghijklmnop1234567890" not in text
    assert "[redacted]" in text


def test_sanitize_error_redacts_db_connection_strings():
    text = js.sanitize_error(Exception("could not connect: postgresql://user:hunter2@dbhost:5432/mydb"))
    assert "hunter2" not in text
    assert "[redacted]" in text


def test_sanitize_error_truncates_long_messages():
    text = js.sanitize_error(Exception("x" * 10000))
    assert len(text) < 10000


def test_fail_step_persists_sanitized_error(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 1)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        step_id = job.steps[0].id
        js.claim_step(db, step_id, "worker-1")
        js.fail_step(db, step_id, "worker-1", Exception("token: sk-verysecretkeyvalue1234567890"))

        step = db.get(JobStep, step_id)
        assert "sk-verysecretkeyvalue1234567890" not in step.last_error
    finally:
        db.close()


# --- cooperative cancellation ------------------------------------------------

def test_cancel_job_cancels_still_queued_steps_immediately(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 2)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        js.cancel_job(db, job.id)

        db.refresh(job)
        assert job.status == "cancelled"
        assert all(s.status == "cancelled" for s in job.steps)
    finally:
        db.close()


def test_cancel_job_leaves_a_running_step_for_the_task_to_stop_itself(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 1)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        js.claim_step(db, job.steps[0].id, "worker-1")  # now running

        js.cancel_job(db, job.id)

        step = db.get(JobStep, job.steps[0].id)
        assert step.status == "running"  # not force-killed -- cooperative, "between steps"
    finally:
        db.close()


def test_cannot_cancel_an_already_terminal_job(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 1)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        js.claim_step(db, job.steps[0].id, "worker-1")
        js.complete_step(db, job.steps[0].id, "worker-1", {})

        with pytest.raises(js.JobServiceError):
            js.cancel_job(db, job.id)
    finally:
        db.close()


# --- resume: never re-runs succeeded steps --------------------------------

def test_resume_job_only_requeues_non_succeeded_steps(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 2)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        js.claim_step(db, job.steps[0].id, "worker-1")
        js.complete_step(db, job.steps[0].id, "worker-1", {"ok": True})  # lead 0: succeeded
        js.claim_step(db, job.steps[1].id, "worker-1")
        js.fail_step(db, job.steps[1].id, "worker-1", "boom")  # lead 1: failed
        db.refresh(job)
        assert job.status == "failed"

        redispatched = js.resume_job(db, job.id)

        assert len(redispatched) == 1
        assert redispatched[0].id == job.steps[1].id
        succeeded_step = db.get(JobStep, job.steps[0].id)
        assert succeeded_step.status == "succeeded"  # untouched
        assert succeeded_step.result_summary == {"ok": True}
    finally:
        db.close()


def test_resume_job_rejects_a_job_that_is_not_in_a_resumable_state(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 1)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        with pytest.raises(js.JobServiceError):
            js.resume_job(db, job.id)  # still queued -- nothing has failed
    finally:
        db.close()


# --- lease-based abandoned-job recovery -------------------------------------

def test_recover_abandoned_steps_requeues_expired_leases(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 1)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        step_id = job.steps[0].id
        js.claim_step(db, step_id, "worker-1", lease_seconds=1)
        step = db.get(JobStep, step_id)
        step.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        db.commit()

        recovered = js.recover_abandoned_steps(db)

        assert len(recovered) == 1
        assert recovered[0].id == step_id
        reloaded = db.get(JobStep, step_id)
        assert reloaded.status == "queued"
        assert reloaded.lease_owner is None
    finally:
        db.close()


def test_recover_abandoned_steps_marks_needs_review_after_max_attempts(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 1)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        step_id = job.steps[0].id
        step = db.get(JobStep, step_id)
        step.max_attempts = 1
        db.commit()

        js.claim_step(db, step_id, "worker-1", lease_seconds=1)  # attempt_count -> 1, equals max
        step = db.get(JobStep, step_id)
        step.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        db.commit()

        recovered = js.recover_abandoned_steps(db)

        assert recovered == []  # not requeued -- exhausted
        reloaded = db.get(JobStep, step_id)
        assert reloaded.status == "needs_review"
    finally:
        db.close()


def test_recover_abandoned_steps_ignores_still_valid_leases(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 1)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        js.claim_step(db, job.steps[0].id, "worker-1", lease_seconds=600)

        recovered = js.recover_abandoned_steps(db)
        assert recovered == []
    finally:
        db.close()


# --- zombie-worker protection: an expired-then-reclaimed lease cannot be ----
# --- committed against by the worker that lost it ---------------------------

def test_zombie_worker_cannot_complete_a_step_after_its_lease_was_reclaimed(client):
    """Simulates the exact race the milestone calls out: worker A's lease
    expires (e.g. it stalled/GC-paused), worker B recovers and re-claims the
    step, and THEN worker A -- still alive, unaware it lost the lease --
    finally finishes and tries to write its result. Worker A's write must be
    rejected; only worker B's should ever be able to land."""
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 1)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        step_id = job.steps[0].id

        js.claim_step(db, step_id, "worker-A", lease_seconds=1)
        step = db.get(JobStep, step_id)
        step.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)  # simulate time passing
        db.commit()

        js.claim_step(db, step_id, "worker-B")  # B legitimately recovers the abandoned lease

        with pytest.raises(js.LeaseLostError):
            js.complete_step(db, step_id, "worker-A", {"result": "from the zombie"})

        reloaded = db.get(JobStep, step_id)
        assert reloaded.status == "running"  # untouched by A's rejected write
        assert reloaded.lease_owner == "worker-B"

        # B's own completion, using its own valid lease, must still succeed.
        js.complete_step(db, step_id, "worker-B", {"result": "from the legitimate holder"})
        reloaded = db.get(JobStep, step_id)
        assert reloaded.status == "succeeded"
        assert reloaded.result_summary == {"result": "from the legitimate holder"}
    finally:
        db.close()


def test_zombie_worker_cannot_fail_a_step_after_its_lease_was_reclaimed(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 1)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        step_id = job.steps[0].id

        js.claim_step(db, step_id, "worker-A", lease_seconds=1)
        step = db.get(JobStep, step_id)
        step.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        db.commit()
        js.claim_step(db, step_id, "worker-B")

        with pytest.raises(js.LeaseLostError):
            js.fail_step(db, step_id, "worker-A", "zombie's stale failure")

        reloaded = db.get(JobStep, step_id)
        assert reloaded.status == "running"  # A's write had no effect
        assert reloaded.last_error is None
    finally:
        db.close()


def test_zombie_worker_cannot_release_a_lease_it_no_longer_holds(client):
    """retry_step (used on the Celery-level bounded-retry path) must be
    lease-guarded too -- a zombie releasing a lease it no longer owns could
    knock a legitimate newer worker's in-progress claim back to 'queued'."""
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 1)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        step_id = job.steps[0].id

        js.claim_step(db, step_id, "worker-A", lease_seconds=1)
        step = db.get(JobStep, step_id)
        step.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        db.commit()
        js.claim_step(db, step_id, "worker-B")

        with pytest.raises(js.LeaseLostError):
            js.retry_step(db, step_id, "worker-A")

        reloaded = db.get(JobStep, step_id)
        assert reloaded.status == "running"  # B's claim was not knocked back to queued
        assert reloaded.lease_owner == "worker-B"
    finally:
        db.close()


def test_normal_completion_still_works_when_no_race_occurred(client):
    """Sanity check that the lease guard doesn't break the ordinary,
    uncontested case."""
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 1)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", lead_ids)
        step_id = job.steps[0].id
        claimed = js.claim_step(db, step_id, "worker-1")

        step = js.complete_step(db, step_id, claimed.lease_owner, {"ok": True})
        assert step.status == "succeeded"
    finally:
        db.close()
