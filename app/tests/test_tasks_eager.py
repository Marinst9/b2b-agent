"""Tests the actual Celery task functions (claim -> run service -> save ->
dispatch-next chaining) using Celery's eager mode, so `.delay()` runs
synchronously in-process without needing a real broker. This is NOT a
substitute for the real-worker-and-Redis integration tests requested by the
milestone (see tests/integration/README.md) -- it verifies the task
functions' own logic (claiming, chaining, version-safety, cancellation
propagation), not actual queueing/delivery/concurrency behavior, which only
a real broker and worker process can exercise.
"""
import pytest

import celery_app
import database
import job_service as js
import tasks
from modules import web_fetcher
from modules.research_extractor import ExtractionResult, ExtractedFact
from modules.draft_generator import GeneratedDraftResult, DraftClaim


@pytest.fixture(autouse=True)
def _eager_mode():
    celery_app.app.conf.task_always_eager = True
    celery_app.app.conf.task_eager_propagates = True
    yield
    celery_app.app.conf.task_always_eager = False
    celery_app.app.conf.task_eager_propagates = False


def create_campaign(client, name="Task Co", **criteria_kwargs):
    campaign_id = client.post("/campaigns", json={"name": name}).json()["id"]
    body = {"product_service": "B2B analytics"}
    body.update(criteria_kwargs)
    client.post(f"/campaigns/{campaign_id}/criteria", json=body)
    return campaign_id


def import_lead(client, campaign_id, website="acme.example", email="lead@co.example"):
    csv_bytes = f"name,company,industry,country,email,website\nLead,Acme,IT,Macedonia,{email},{website}\n".encode()
    client.post(f"/campaigns/{campaign_id}/leads/import", files={"file": ("leads.csv", csv_bytes, "text/csv")})
    # Filter by email rather than assuming index 0 -- callers may import
    # more than one lead into the same campaign (e.g. to test partial
    # campaign failure), so the most-recently-added lead isn't always last.
    leads = client.get(f"/campaigns/{campaign_id}/leads").json()
    return next(l for l in leads if l["email"] == email)


def get_db():
    return database.SessionLocal()


class FakePage:
    def __init__(self, url, text):
        self.url = url
        self.status_code = 200
        self.content_type = "text/html"
        self.text = text


def mock_research_ok(monkeypatch, facts=None):
    page_text = "Acme sells widgets."
    page = FakePage("https://acme.example/", f"<html><body>{page_text}</body></html>")

    def _complete_fact(f):
        return {
            "category": f["category"],
            "key": f.get("key"),
            "text": f["text"],
            "excerpt": f.get("excerpt", page_text),
            "source_url": f.get("source_url", page.url),
        }

    complete_facts = [_complete_fact(f) for f in (facts or [])]
    monkeypatch.setattr(tasks.research_service.web_fetcher, "fetch_url", lambda url, **k: page)
    monkeypatch.setattr(
        tasks.research_service.research_extractor,
        "extract_facts",
        lambda text, url, **k: ExtractionResult(facts=complete_facts),
    )


def mock_draft_ok(monkeypatch, subject="Hi", body="We can help."):
    def fake(*, company, contact_name, product_service, facts, language, tone, length, call_model=None):
        return GeneratedDraftResult(subject, body, [], "gpt-4o-mini", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    monkeypatch.setattr(tasks.draft_service.draft_generator, "generate_draft", fake)


# --- single-step tasks: claim, run, complete --------------------------------

def test_research_task_completes_step_and_records_summary(client, monkeypatch):
    mock_research_ok(monkeypatch, facts=[{"category": "offering", "text": "Sells widgets"}])
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", [lead["id"]])
        step_id = job.steps[0].id
        result = tasks.run_research_step.apply(args=[job.id, step_id]).get()
        assert result["status"] == "completed"

        db.expire_all()
        step = db.get(database.JobStep, step_id)
        assert step.status == "succeeded"
        assert step.result_summary["status"] == "completed"
    finally:
        db.close()


def test_draft_task_completes_and_persists_a_version(client, monkeypatch):
    mock_draft_ok(monkeypatch)
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "draft", [lead["id"]])
        step_id = job.steps[0].id
        tasks.run_draft_step.apply(args=[job.id, step_id]).get()

        db.expire_all()
        step = db.get(database.JobStep, step_id)
        assert step.status == "succeeded"
        assert step.result_summary["applied"] is True

        lead_row = db.get(database.Lead, lead["id"])
        assert lead_row.draft.version == 1
        assert lead_row.draft.subject == "Hi"
    finally:
        db.close()


# --- duplicate task delivery -------------------------------------------------

def test_duplicate_delivery_of_the_same_task_is_a_safe_no_op(client, monkeypatch):
    mock_research_ok(monkeypatch)
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", [lead["id"]])
        step_id = job.steps[0].id

        first = tasks.run_research_step.apply(args=[job.id, step_id]).get()
        second = tasks.run_research_step.apply(args=[job.id, step_id]).get()  # simulated redelivery

        assert "skipped" in second
        assert "skipped" not in first

        research = db.query(database.CompanyResearch).filter_by(lead_id=lead["id"]).first()
        assert research.version == 1  # not run twice
    finally:
        db.close()


# --- workflow chaining and cancellation between steps -----------------------

def test_workflow_chains_research_then_qualify_then_draft(client, monkeypatch):
    mock_research_ok(monkeypatch, facts=[{"category": "offering", "text": "Sells widgets"}])
    mock_draft_ok(monkeypatch)
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "workflow", [lead["id"]])
        research_step = next(s for s in job.steps if s.step_type == "research")
        tasks.run_research_step.apply(args=[job.id, research_step.id]).get()

        db.expire_all()
        qualify_step = next(s for s in job.steps if s.step_type == "qualify")
        draft_step = next(s for s in job.steps if s.step_type == "draft")
        assert qualify_step.status == "succeeded"  # dispatched and run automatically
        assert draft_step.status == "succeeded"

        db.refresh(job)
        assert job.status == "succeeded"
    finally:
        db.close()


def test_cancelling_a_workflow_job_stops_it_before_the_next_step(client, monkeypatch):
    mock_research_ok(monkeypatch)
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "workflow", [lead["id"]])
        research_step = next(s for s in job.steps if s.step_type == "research")

        # Cancel the job BEFORE the research step actually runs -- simulates
        # a user hitting cancel while the first step is still in flight/about
        # to be claimed; job_service.cancel_job only force-cancels 'queued'
        # steps, so we cancel first, then run the already-dispatched task.
        js.cancel_job(db, job.id)

        tasks.run_research_step.apply(args=[job.id, research_step.id]).get()

        db.expire_all()
        qualify_step = next(s for s in job.steps if s.step_type == "qualify")
        draft_step = next(s for s in job.steps if s.step_type == "draft")
        assert qualify_step.status == "cancelled"
        assert draft_step.status == "cancelled"
    finally:
        db.close()


# --- partial campaign failure: one lead fails, others continue --------------

def test_one_leads_unexpected_failure_does_not_affect_other_leads(client, monkeypatch):
    """research_service.run_research is designed to swallow crawl/extraction
    failures internally (a failed crawl is a normal, recoverable research
    OUTCOME, not a task-level exception) -- so to exercise a genuine
    task-level "stopping" failure we simulate a bug in the service call
    itself, distinct from the already-covered "website unreachable" /
    "missing website" continue-with-flags outcomes."""
    campaign_id = create_campaign(client)
    lead_ok = import_lead(client, campaign_id, email="ok@co.example", website="good.example")
    lead_bad = import_lead(client, campaign_id, email="bad@co.example", website="bad.example")
    mock_research_ok(monkeypatch)

    real_run_research = tasks.research_service.run_research

    def buggy_run_research(db, lead_id, **kwargs):
        if lead_id == lead_bad["id"]:
            raise RuntimeError("unexpected bug in research_service")
        return real_run_research(db, lead_id, **kwargs)

    monkeypatch.setattr(tasks.research_service, "run_research", buggy_run_research)

    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", [lead_ok["id"], lead_bad["id"]])
        for step in job.steps:
            tasks.run_research_step.apply(args=[job.id, step.id]).get()

        db.expire_all()
        steps_by_lead = {s.lead_id: s for s in job.steps}
        assert steps_by_lead[lead_ok["id"]].status == "succeeded"
        assert steps_by_lead[lead_bad["id"]].status in ("needs_review", "failed")

        db.refresh(job)
        assert job.status == "needs_review"  # overall job reflects the mixed outcome, not silently "succeeded"
    finally:
        db.close()


def test_stopping_failure_cancels_downstream_steps_for_that_lead_only(client, monkeypatch):
    campaign_id = create_campaign(client)
    lead_ok = import_lead(client, campaign_id, email="ok@co.example", website="good.example")
    lead_bad = import_lead(client, campaign_id, email="bad@co.example", website="bad.example")
    mock_research_ok(monkeypatch)
    mock_draft_ok(monkeypatch)

    real_run_research = tasks.research_service.run_research

    def buggy_run_research(db, lead_id, **kwargs):
        if lead_id == lead_bad["id"]:
            raise RuntimeError("unexpected bug in research_service")
        return real_run_research(db, lead_id, **kwargs)

    monkeypatch.setattr(tasks.research_service, "run_research", buggy_run_research)

    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "workflow", [lead_ok["id"], lead_bad["id"]])
        research_steps = [s for s in job.steps if s.step_type == "research"]
        for step in research_steps:
            tasks.run_research_step.apply(args=[job.id, step.id]).get()

        db.expire_all()
        bad_qualify = next(s for s in job.steps if s.lead_id == lead_bad["id"] and s.step_type == "qualify")
        bad_draft = next(s for s in job.steps if s.lead_id == lead_bad["id"] and s.step_type == "draft")
        ok_qualify = next(s for s in job.steps if s.lead_id == lead_ok["id"] and s.step_type == "qualify")

        assert bad_qualify.status == "cancelled"
        assert bad_draft.status == "cancelled"
        assert ok_qualify.status == "succeeded"  # the other lead's chain proceeded normally
    finally:
        db.close()


# --- missing website: an "allowed to continue with generic draft" outcome ----

def test_missing_website_does_not_stop_the_workflow_it_flows_to_generic_draft(client, monkeypatch):
    mock_draft_ok(monkeypatch)
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id, website="")  # no website -- never guessed

    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "workflow", [lead["id"]])
        research_step = next(s for s in job.steps if s.step_type == "research")
        tasks.run_research_step.apply(args=[job.id, research_step.id]).get()

        db.expire_all()
        qualify_step = next(s for s in job.steps if s.step_type == "qualify")
        draft_step = next(s for s in job.steps if s.step_type == "draft")
        assert qualify_step.status == "succeeded"
        assert draft_step.status == "succeeded"

        lead_row = db.get(database.Lead, lead["id"])
        assert lead_row.draft.latest_version().is_generic_fallback is True
        flag_types = {f["type"] for f in lead_row.draft.latest_version().review_flags}
        assert "missing_research" in flag_types
    finally:
        db.close()


# --- version safety: a late worker result never clobbers a manual edit -----

def test_late_draft_result_does_not_overwrite_a_manual_edit_made_meanwhile(client, monkeypatch):
    # No mock_draft_ok here on purpose: each generate_draft*() call below
    # passes its own explicit call_model to distinguish "the manual edit"
    # from "the late background result" -- mock_draft_ok replaces the outer
    # draft_generator.generate_draft function entirely and would ignore that.
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "draft", [lead["id"]])
        step_id = job.steps[0].id

        # Simulate: the task claims the step and captures its version
        # baseline, THEN (before it finishes) a human manually edits the
        # draft via the synchronous API -- advancing the draft version.
        claimed = js.claim_step(db, step_id, "worker-1")
        base_version = tasks.draft_service.current_draft_version(db, lead["id"])
        assert base_version == 0  # no draft yet

        # The manual edit path requires an existing draft; simulate it by
        # generating one synchronously first (a real "regenerate" click),
        # which is the newer state the background result must not clobber.
        import draft_service as ds

        ds.generate_draft(db, lead["id"], call_model=lambda prompt: (__import__("json").dumps({"subject": "Manual v1", "body": "Manual body", "claims": []}), {}))

        db.expire_all()
        result = ds.generate_draft_if_still_applicable(
            db, lead["id"], base_version, language="en", tone="professional", length="medium",
            call_model=lambda prompt: (__import__("json").dumps({"subject": "Background subject", "body": "BG body", "claims": []}), {}),
        )

        assert result["applied"] is False
        lead_row = db.get(database.Lead, lead["id"])
        assert lead_row.draft.version == 1
        assert lead_row.draft.subject == "Manual v1"  # the manual edit is preserved, not overwritten
    finally:
        db.close()


def test_approval_survives_a_superseded_late_background_result(client, monkeypatch):
    """A late, superseded background result must never unexpectedly
    invalidate an approval the user already made on the current version."""
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    db = get_db()
    try:
        import draft_service as ds
        import json

        ds.generate_draft(db, lead["id"], call_model=lambda prompt: (json.dumps({"subject": "S1", "body": "B1", "claims": []}), {}))
        lead_row = db.get(database.Lead, lead["id"])
        ds.approve_draft(db, lead_row.draft.id)

        # A stale background job (dispatched before approval, finishing after)
        # tries to save a result based on the pre-approval version.
        result = ds.generate_draft_if_still_applicable(
            db, lead["id"], expected_base_version=0,  # stale baseline
            call_model=lambda prompt: (json.dumps({"subject": "Stale", "body": "Stale body", "claims": []}), {}),
        )

        assert result["applied"] is False
        db.expire_all()
        lead_row = db.get(database.Lead, lead["id"])
        assert lead_row.draft.is_approved_for_send() is True  # untouched
    finally:
        db.close()


# --- no email is ever sent from a processing job -----------------------------

def test_workflow_tasks_never_call_email_sender(client, monkeypatch):
    from modules import email_sender

    send_calls = []
    monkeypatch.setattr(email_sender, "send_email", lambda *a, **k: send_calls.append(a) or True)
    mock_research_ok(monkeypatch, facts=[{"category": "offering", "text": "Sells widgets"}])
    mock_draft_ok(monkeypatch)

    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "workflow", [lead["id"]])
        research_step = next(s for s in job.steps if s.step_type == "research")
        tasks.run_research_step.apply(args=[job.id, research_step.id]).get()
    finally:
        db.close()

    assert send_calls == []


# --- resume: only redispatches steps whose prerequisite already succeeded ----

def test_resuming_after_qualify_failure_does_not_rerun_research_and_correctly_resumes_qualify(client, monkeypatch):
    """Regression for a dispatch-ordering bug: a naive "always start from the
    first step type" resume would either rerun research needlessly or fail
    to progress qualify/draft at all. The correct behavior is to redispatch
    exactly the steps that are 'queued' and whose predecessor (if any) has
    already succeeded."""
    mock_research_ok(monkeypatch, facts=[{"category": "offering", "text": "Sells widgets"}])
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)

    real_qualify_lead = tasks.qualification_service.qualify_lead

    def broken_qualify_lead(db_, lead_id):
        raise RuntimeError("simulated qualify bug")

    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "workflow", [lead["id"]])
        research_step = next(s for s in job.steps if s.step_type == "research")

        monkeypatch.setattr(tasks.qualification_service, "qualify_lead", broken_qualify_lead)
        tasks.run_research_step.apply(args=[job.id, research_step.id]).get()

        db.expire_all()
        qualify_step = next(s for s in job.steps if s.step_type == "qualify")
        assert qualify_step.status == "needs_review"  # simulated bug -> qualify failed
        draft_step = next(s for s in job.steps if s.step_type == "draft")
        assert draft_step.status == "cancelled"  # was skipped when qualify failed

        research_run_count = {"n": 0}
        real_run_research = tasks.research_service.run_research

        def counting_run_research(db_, lead_id, **kwargs):
            research_run_count["n"] += 1
            return real_run_research(db_, lead_id, **kwargs)

        monkeypatch.setattr(tasks.research_service, "run_research", counting_run_research)
        monkeypatch.setattr(tasks.qualification_service, "qualify_lead", real_qualify_lead)  # bug is "fixed" before resuming
        mock_draft_ok(monkeypatch)

        redispatched = js.resume_job(db, job.id)
        assert {s.step_type for s in redispatched} == {"qualify", "draft"}  # NOT research -- already succeeded
        db.commit()

        ok = tasks.dispatch_new_job(db, job.id)
        assert ok is True

        db.expire_all()
        assert research_run_count["n"] == 0  # research was never rerun
        final_qualify = next(s for s in job.steps if s.step_type == "qualify")
        final_draft = next(s for s in job.steps if s.step_type == "draft")
        assert final_qualify.status == "succeeded"
        assert final_draft.status == "succeeded"
    finally:
        db.close()


# --- bounded retries: decision logic -----------------------------------------
#
# NOTE on why this is tested at the `_handle_task_exception` level rather
# than by letting a task actually retry end-to-end: Celery's eager mode does
# NOT loop on self.retry() -- a retryable exception just raises `Retry`
# straight back to the caller (there is no broker/worker loop to reschedule
# through), which is a real, documented eager-mode limitation, not a bug in
# this code. Whether Celery itself correctly reschedules and a real worker
# picks the retried task back up is exactly the kind of thing eager mode
# cannot prove -- that is covered instead by
# tests/integration/test_real_broker.py (not run in this environment; see
# tests/integration/README.md). What CAN and should be unit-tested here is
# OUR OWN decision logic: does a retryable, non-exhausted failure correctly
# release the step back to 'queued' (so a real retry could re-claim it)
# rather than marking it terminally failed?

class _FakeRequest:
    def __init__(self, retries):
        self.retries = retries


class _FakeCeleryTask:
    def __init__(self, retries, max_retries):
        self.request = _FakeRequest(retries)
        self.max_retries = max_retries


def test_retryable_non_final_failure_releases_step_to_queued_and_reraises(client):
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", [lead["id"]])
        step_id = job.steps[0].id
        js.claim_step(db, step_id, "worker-1")

        fake_task = _FakeCeleryTask(retries=0, max_retries=tasks.TASK_MAX_RETRIES)
        with pytest.raises(ConnectionError):
            tasks._handle_task_exception(fake_task, step_id, job.id, lead["id"], "research", "worker-1", ConnectionError("blip"))

        db.expire_all()
        step = db.get(database.JobStep, step_id)
        assert step.status == "queued"  # released, NOT failed -- a real retry could re-claim it
        assert step.lease_owner is None
    finally:
        db.close()


def test_retryable_final_attempt_marks_needs_review_and_does_not_raise(client):
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "workflow", [lead["id"]])
        research_step = next(s for s in job.steps if s.step_type == "research")
        js.claim_step(db, research_step.id, "worker-1")

        fake_task = _FakeCeleryTask(retries=tasks.TASK_MAX_RETRIES, max_retries=tasks.TASK_MAX_RETRIES)
        result = tasks._handle_task_exception(fake_task, research_step.id, job.id, lead["id"], "research", "worker-1", ConnectionError("persistent blip"))

        assert result["retryable"] is True
        assert result["final_attempt"] is True

        db.expire_all()
        step = db.get(database.JobStep, research_step.id)
        assert step.status == "needs_review"  # bounded -- exhausted, not retried forever
        assert "persistent blip" in step.last_error

        qualify_step = next(s for s in job.steps if s.step_type == "qualify")
        assert qualify_step.status == "cancelled"  # downstream steps for this lead were skipped
    finally:
        db.close()


def test_non_retryable_exception_is_terminal_on_the_very_first_attempt(client):
    """A non-retryable exception type must go straight to terminal even on
    attempt 0 -- it must never be retried regardless of max_retries."""
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "research", [lead["id"]])
        step_id = job.steps[0].id
        js.claim_step(db, step_id, "worker-1")

        fake_task = _FakeCeleryTask(retries=0, max_retries=tasks.TASK_MAX_RETRIES)
        result = tasks._handle_task_exception(fake_task, step_id, job.id, lead["id"], "research", "worker-1", RuntimeError("a real bug"))

        assert result["retryable"] is False
        db.expire_all()
        step = db.get(database.JobStep, step_id)
        assert step.status == "needs_review"
    finally:
        db.close()


# --- dispatch recovery must cover every pending step, not just the ----------
# --- initial job publication -------------------------------------------------

def test_mid_workflow_dispatch_failure_does_not_corrupt_the_succeeded_step(client, monkeypatch):
    """If publishing the NEXT step (qualify) fails right after the CURRENT
    step (research) already succeeded, the research step's own success must
    be completely unaffected -- this is the fix for a real bug found during
    verification: a naive implementation let the broker failure while
    dispatching qualify propagate up and get treated as if research itself
    had failed."""
    mock_research_ok(monkeypatch, facts=[{"category": "offering", "text": "Sells widgets"}])
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)

    monkeypatch.setattr(tasks.run_qualify_step, "delay", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("broker blip")))

    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "workflow", [lead["id"]])
        research_step = next(s for s in job.steps if s.step_type == "research")
        result = tasks.run_research_step.apply(args=[job.id, research_step.id]).get()

        assert result["status"] == "completed"  # research's own outcome is untouched

        db.expire_all()
        final_research = next(s for s in job.steps if s.step_type == "research")
        qualify_step = next(s for s in job.steps if s.step_type == "qualify")
        assert final_research.status == "succeeded"
        assert qualify_step.status == "queued"  # not lost, not corrupted -- just not yet delivered
    finally:
        db.close()


def test_sweep_recovers_a_stuck_mid_workflow_step_and_completes_the_chain(client, monkeypatch):
    """The periodic sweep must find and redispatch a step that is 'queued'
    and ready (its predecessor already succeeded) but was never actually
    published -- not just jobs whose very FIRST dispatch failed."""
    mock_research_ok(monkeypatch, facts=[{"category": "offering", "text": "Sells widgets"}])
    mock_draft_ok(monkeypatch)
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)

    real_qualify_delay = tasks.run_qualify_step.delay
    monkeypatch.setattr(tasks.run_qualify_step, "delay", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("broker blip")))

    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "workflow", [lead["id"]])
        # Simulate that this job's INITIAL dispatch already succeeded (as it
        # would have in the real failure this reproduces: research got
        # published and ran fine; only the LATER mid-chain dispatch to
        # qualify failed) -- so the sweep's "mid-chain" pass is the one
        # actually being exercised here, not the "never dispatched at all"
        # (job.dispatched=False) path, which is a different, already-tested
        # code path (see job_service tests).
        job.dispatched = True
        db.commit()
        research_step = next(s for s in job.steps if s.step_type == "research")
        tasks.run_research_step.apply(args=[job.id, research_step.id]).get()

        db.expire_all()
        qualify_step = next(s for s in job.steps if s.step_type == "qualify")
        assert qualify_step.status == "queued"  # stuck: research succeeded, but qualify was never delivered
    finally:
        db.close()

    monkeypatch.setattr(tasks.run_qualify_step, "delay", real_qualify_delay)  # broker "recovers"
    sweep_result = tasks.sweep_dispatch_outbox()
    assert job.id in sweep_result["mid_chain_redispatched"]

    db = get_db()
    try:
        db.expire_all()
        final_job = db.get(database.Job, job.id)
        assert final_job.status == "succeeded"
        assert {s.step_type: s.status for s in final_job.steps} == {"research": "succeeded", "qualify": "succeeded", "draft": "succeeded"}
    finally:
        db.close()


def test_publish_job_only_dispatches_steps_whose_predecessor_already_succeeded(client, monkeypatch):
    """Isolates the readiness-filtering logic from eager-mode's tendency to
    cascade a whole chain synchronously the moment the first step runs: here
    we replace _dispatch_step_task itself with a recorder, so we can see
    EXACTLY which (step_type, step_id) pairs _publish_job decided were ready
    -- a 'queued' draft/qualify step whose predecessor hasn't succeeded yet
    must never be among them."""
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)

    dispatched = []
    monkeypatch.setattr(tasks, "_dispatch_step_task", lambda step_type, job_id, step_id: dispatched.append(step_type))

    db = get_db()
    try:
        job = js.create_job(db, campaign_id, "workflow", [lead["id"]])
        job_id = job.id
    finally:
        db.close()

    tasks._publish_job(job_id)

    assert dispatched == ["research"]  # qualify/draft are 'queued' too, but not yet ready
