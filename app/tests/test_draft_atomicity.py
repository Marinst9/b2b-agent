"""Verifies the draft version bump is a true atomic compare-and-swap, not a
check-then-act pattern with a race window a concurrent writer could land in.
"""
import json

import pytest

import database
import draft_service as ds


def create_campaign(client, name="Atomicity Co"):
    campaign_id = client.post("/campaigns", json={"name": name}).json()["id"]
    client.post(f"/campaigns/{campaign_id}/criteria", json={"product_service": "B2B analytics"})
    return campaign_id


def import_lead(client, campaign_id, email="lead@co.example"):
    csv_bytes = f"name,company,industry,country,email,website\nLead,Acme,IT,Macedonia,{email},\n".encode()
    client.post(f"/campaigns/{campaign_id}/leads/import", files={"file": ("leads.csv", csv_bytes, "text/csv")})
    return client.get(f"/campaigns/{campaign_id}/leads").json()[0]


def fake_call_model(subject, body):
    return lambda prompt: (json.dumps({"subject": subject, "body": body, "claims": []}), {})


def get_db():
    return database.SessionLocal()


# --- the exact race: worker's initial read, then a manual edit commits ------

def test_edit_committed_between_workers_read_and_write_is_preserved(client):
    """The precise scenario requested: a background worker reads the base
    version, a manual edit commits in the meantime (advancing the version),
    and the worker's stale write must be rejected -- not silently applied,
    not silently merged, not crashing."""
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    db = get_db()
    try:
        # Worker's "initial read": no draft exists yet, so its baseline is 0.
        base_version = ds.current_draft_version(db, lead["id"])
        assert base_version == 0

        # A manual edit (or a concurrent regenerate) commits in the meantime.
        ds.generate_draft(db, lead["id"], call_model=fake_call_model("Manual", "Manual body"))

        # The worker now attempts its write, using its STALE base_version.
        result = ds.generate_draft_if_still_applicable(
            db, lead["id"], base_version, call_model=fake_call_model("Worker result", "Worker body")
        )

        assert result["applied"] is False
        assert result["current_version"] == 1

        db.expire_all()
        lead_row = db.get(database.Lead, lead["id"])
        assert lead_row.draft.version == 1
        assert lead_row.draft.subject == "Manual"  # never overwritten
    finally:
        db.close()


# --- true atomicity: no separate check-then-act window ----------------------

def test_concurrent_writers_from_the_same_base_version_only_one_wins(client):
    """Two 'workers' both compute expected_base_version=0 (as if they read
    the draft at the same instant, before either wrote). If the version
    check and the write were separate steps, both could pass the check
    before either commits. With the atomic CAS, only the first UPDATE can
    ever match version=0; the second's rowcount is 0."""
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    db = get_db()
    try:
        content_a = ds._generate_and_verify_content(
            db.get(database.Lead, lead["id"]),
            ds._build_evidence_context(db, db.get(database.Lead, lead["id"])),
            "en", "professional", "medium",
            call_model=fake_call_model("Worker A", "Body A"),
        )
        content_b = ds._generate_and_verify_content(
            db.get(database.Lead, lead["id"]),
            ds._build_evidence_context(db, db.get(database.Lead, lead["id"])),
            "en", "professional", "medium",
            call_model=fake_call_model("Worker B", "Body B"),
        )

        draft_a = ds._try_bump_draft_atomically(db, lead["id"], 0, **content_a)
        draft_b = ds._try_bump_draft_atomically(db, lead["id"], 0, **content_b)  # same expected_base_version=0

        assert draft_a is not None
        assert draft_b is None  # lost the race -- version had already moved to 1

        db.expire_all()
        lead_row = db.get(database.Lead, lead["id"])
        assert lead_row.draft.version == 1
        assert lead_row.draft.subject == "Worker A"  # exactly one winner, no partial/merged state
    finally:
        db.close()


# --- approval binds to exact content; a concurrent edit invalidates it ------

def test_approve_then_concurrent_edit_invalidates_approval_not_the_reverse(client):
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    db = get_db()
    try:
        ds.generate_draft(db, lead["id"], call_model=fake_call_model("S1", "B1"))
        lead_row = db.get(database.Lead, lead["id"])
        approved = ds.approve_draft(db, lead_row.draft.id)
        assert approved.is_approved_for_send() is True

        # A concurrent edit lands after approval.
        ds.edit_draft(db, lead_row.draft.id, "S2", "B2")

        db.expire_all()
        lead_row = db.get(database.Lead, lead["id"])
        assert lead_row.draft.is_approved_for_send() is False  # correctly invalidated
        assert lead_row.draft.version == 2
        assert lead_row.draft.subject == "S2"  # the edit itself is never lost
    finally:
        db.close()


def test_generate_draft_recovers_via_retry_when_edit_wins_the_first_race(client, monkeypatch):
    """_persist_new_version (used by generate_draft/edit_draft, i.e. live
    synchronous user actions) retries once against the freshest version
    rather than dropping the user's action -- unlike the background-worker
    path, which must fail closed instead."""
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    db = get_db()
    try:
        ds.generate_draft(db, lead["id"], call_model=fake_call_model("First", "Body"))

        call_count = {"n": 0}
        real_bump = ds._try_bump_draft_atomically

        def flaky_once_then_real(db_, lead_id, expected_base_version, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return None  # simulate losing the race exactly once
            return real_bump(db_, lead_id, expected_base_version, **kwargs)

        monkeypatch.setattr(ds, "_try_bump_draft_atomically", flaky_once_then_real)

        draft = ds.generate_draft(db, lead["id"], call_model=fake_call_model("Second", "Body 2"))
        assert draft.subject == "Second"  # succeeded on retry rather than raising
        assert call_count["n"] == 2
    finally:
        db.close()


def test_generate_draft_raises_a_clear_error_if_it_keeps_losing_the_race(client, monkeypatch):
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    db = get_db()
    try:
        monkeypatch.setattr(ds, "_try_bump_draft_atomically", lambda *a, **k: None)  # always loses
        with pytest.raises(ds.DraftServiceError):
            ds.generate_draft(db, lead["id"], call_model=fake_call_model("X", "Y"))
    finally:
        db.close()
