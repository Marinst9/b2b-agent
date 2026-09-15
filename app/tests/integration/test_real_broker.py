"""Integration tests requiring a REAL Redis broker and a REAL, separate
Linux worker process consuming from it (the `worker-integration` container
in ../../../docker-compose.integration.yml) -- not eager mode, and not an
in-process pytest worker thread running natively on the host. See
README.md in this directory for the current not-run status in this
environment and exact setup commands.

Because the worker runs in a genuinely separate process (a separate
container, potentially a separate machine), `monkeypatch` in this test
process cannot affect it. External providers are mocked INSIDE that
container instead, by container_fakes.py/container_entrypoint.py -- see
those files for how. These tests only publish work (via the API or by
calling `.delay()`, which just serializes a message onto the real broker)
and then observe outcomes through the shared disposable Postgres database;
they never call task/service functions directly in this process.
"""
import time
import uuid

import celery_app
import database
import job_service as js
import tasks


def create_campaign(client, name="Integration Co"):
    campaign_id = client.post("/campaigns", json={"name": name}).json()["id"]
    client.post(f"/campaigns/{campaign_id}/criteria", json={"product_service": "B2B analytics"})
    return campaign_id


def import_lead(client, campaign_id, email="lead@co.example", website="", company="Acme"):
    csv_bytes = (
        f"name,company,industry,country,email,website\nLead,{company},IT,Macedonia,{email},{website}\n"
    ).encode()
    client.post(f"/campaigns/{campaign_id}/leads/import", files={"file": ("leads.csv", csv_bytes, "text/csv")})
    leads = client.get(f"/campaigns/{campaign_id}/leads").json()
    return next(l for l in leads if l["email"] == email)


def wait_for_terminal(job_id, timeout=25):
    """Polls until the job reaches a terminal status, then force-loads its
    steps (and the scalar attributes callers read off of them) before the
    session closes -- the returned `Job` is detached afterwards, and a
    relationship SQLAlchemy hasn't loaded yet raises DetachedInstanceError
    on first access from a closed session, rather than lazily reconnecting."""
    deadline = time.time() + timeout
    db = database.SessionLocal()
    try:
        while time.time() < deadline:
            db.expire_all()
            job = db.get(database.Job, job_id)
            if job.status in js.TERMINAL_STATUSES:
                for s in job.steps:
                    _ = (s.step_type, s.status, s.attempt_count)
                return job
            time.sleep(0.2)
        raise TimeoutError(f"job {job_id} did not reach a terminal state within {timeout}s")
    finally:
        db.close()


def test_task_dispatched_over_real_broker_is_consumed_by_a_real_worker(client):
    """The core round-trip this whole milestone depends on: a job created
    via the API is actually published to Redis, actually picked up by a
    SEPARATE, genuinely containerized Linux worker over the network, and its
    result actually lands back in Postgres -- none of which the eager-mode
    tests can prove."""
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id, company="Acme Real Worker Co")

    db = database.SessionLocal()
    try:
        job = js.create_job(db, campaign_id, "draft", [lead["id"]])
        job_id = job.id
        ok = tasks.dispatch_new_job(db, job_id)
        assert ok is True  # a real broker publish actually succeeds here
    finally:
        db.close()

    finished = wait_for_terminal(job_id)
    assert finished.status == "succeeded"

    db = database.SessionLocal()
    try:
        lead_row = db.get(database.Lead, lead["id"])
        # This exact subject only comes from container_fakes.py's fake
        # generate_draft, which runs INSIDE the worker-integration
        # container -- proving the result really was produced by the real,
        # separate worker process, not by this test process.
        assert lead_row.draft.subject == "Draft for Acme Real Worker Co"
    finally:
        db.close()


def test_workflow_chain_completes_end_to_end_over_a_real_worker(client):
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id, email="workflow@co.example", website="acme.example", company="Chain Co")

    db = database.SessionLocal()
    try:
        job = js.create_job(db, campaign_id, "workflow", [lead["id"]])
        job_id = job.id
        assert tasks.dispatch_new_job(db, job_id) is True
    finally:
        db.close()

    finished = wait_for_terminal(job_id, timeout=30)
    assert finished.status == "succeeded"
    assert {s.step_type: s.status for s in finished.steps} == {
        "research": "succeeded",
        "qualify": "succeeded",
        "draft": "succeeded",
    }


def test_duplicate_delivery_over_a_real_broker_still_does_not_duplicate_work(client):
    """Publishes the SAME step twice (simulating Celery's at-least-once
    redelivery) over the real broker and confirms only one of the two
    resulting real worker executions actually does the work."""
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id, email="dup@co.example", company="Dup Co")

    db = database.SessionLocal()
    try:
        job = js.create_job(db, campaign_id, "draft", [lead["id"]])
        job_id = job.id
        step_id = job.steps[0].id
    finally:
        db.close()

    # .delay() here only serializes and publishes onto the real broker --
    # the actual execution happens entirely inside worker-integration.
    tasks.run_draft_step.delay(job_id, step_id)
    tasks.run_draft_step.delay(job_id, step_id)  # simulated redelivery, published separately

    finished = wait_for_terminal(job_id)
    assert finished.status == "succeeded"

    db = database.SessionLocal()
    try:
        lead_row = db.get(database.Lead, lead["id"])
        assert lead_row.draft.version == 1  # not duplicated into version 2
    finally:
        db.close()


def test_transient_failure_actually_retries_and_succeeds_via_a_real_worker(client):
    """This is the ONE thing eager mode genuinely cannot prove (see the note
    in tests/test_tasks_eager.py): Celery's self.retry() only actually loops
    -- rescheduling through the broker and being picked back up by a worker
    -- when there IS a real broker and a real worker. Eager mode just raises
    `Retry` back to the caller instead.

    The flakiness itself is driven by container_fakes.py's fake
    generate_draft, which fails exactly once (tracked via a Redis counter
    shared with the worker container, keyed by a unique id embedded in the
    lead's company name) before succeeding -- this test process never
    monkeypatches anything the worker would see, since it couldn't."""
    unique_id = uuid.uuid4().hex
    flaky_company = f"FLAKY:1:{unique_id}"  # fail once, succeed on the real scheduled retry

    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id, email="retry@co.example", company=flaky_company)

    db = database.SessionLocal()
    try:
        job = js.create_job(db, campaign_id, "draft", [lead["id"]])
        job_id = job.id
        assert tasks.dispatch_new_job(db, job_id) is True
    finally:
        db.close()

    finished = wait_for_terminal(job_id, timeout=25)
    assert finished.status == "succeeded"
    assert finished.steps[0].attempt_count == 2  # failed once, succeeded on the 2nd (real, scheduled) attempt

    import redis

    r = redis.Redis.from_url(celery_app.app.conf.broker_url)
    assert int(r.get(f"integration-fake:flaky-draft:{unique_id}")) == 2  # exactly one retry occurred, not more
