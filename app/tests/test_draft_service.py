from datetime import datetime, timedelta, timezone

import draft_service
from modules.draft_generator import GeneratedDraftResult, DraftClaim, DraftGenerationError


def create_campaign(client, name="Draft Co", **criteria_kwargs):
    campaign_id = client.post("/campaigns", json={"name": name}).json()["id"]
    body = {"product_service": "B2B analytics platform"}
    body.update(criteria_kwargs)
    client.post(f"/campaigns/{campaign_id}/criteria", json=body)
    return campaign_id


def import_lead(client, campaign_id, website="acme.example", email="lead@co.example", industry="IT", country="Macedonia"):
    csv_bytes = (
        f"name,company,industry,country,email,website\n"
        f"Lead Person,Acme,{industry},{country},{email},{website}\n"
    ).encode()
    client.post(f"/campaigns/{campaign_id}/leads/import", files={"file": ("leads.csv", csv_bytes, "text/csv")})
    return client.get(f"/campaigns/{campaign_id}/leads").json()[0]


def fake_generator(subject="Hello", body="We can help.", claims=None, usage=None):
    def _fake(*, company, contact_name, product_service, facts, language, tone, length, call_model=None):
        return GeneratedDraftResult(
            subject,
            body,
            [DraftClaim(**c) for c in (claims or [])],
            "gpt-4o-mini",
            usage or {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        )

    return _fake


def add_research_with_facts(db_session_factory, lead_id, facts, status="completed", has_conflicts=False, expires_in_seconds=3600):
    import database

    db = db_session_factory()
    try:
        lead = db.get(database.Lead, lead_id)
        research = database.CompanyResearch(
            lead_id=lead_id,
            website=lead.website or "acme.example",
            status=status,
            version=1,
            pages_fetched=1,
            offerings_unknown=not any(f.get("category") == "offering" for f in facts),
            target_customers_unknown=not any(f.get("category") == "target_customer" for f in facts),
            has_conflicts=has_conflicts,
            completed_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds),
        )
        db.add(research)
        db.flush()
        fact_rows = []
        for f in facts:
            row = database.ResearchFact(
                research_id=research.id,
                category=f["category"],
                key=f.get("key"),
                text=f["text"],
                excerpt=f.get("excerpt", f["text"][:20]),
                source_url=f.get("source_url", "https://acme.example/"),
                retrieved_at=datetime.now(timezone.utc),
                conflicting=f.get("conflicting", False),
            )
            db.add(row)
            fact_rows.append(row)
        db.commit()
        for r in fact_rows:
            db.refresh(r)
        return [r.id for r in fact_rows]
    finally:
        db.close()


def get_db_session():
    import database

    return database.SessionLocal


# --- generation inputs: product/service, criteria, research, qualification ----

def test_generation_requires_campaign_criteria(client):
    campaign_id = client.post("/campaigns", json={"name": "No Criteria"}).json()["id"]
    lead = import_lead(client, campaign_id)
    resp = client.post(f"/campaigns/{campaign_id}/drafts/generate", json={"lead_ids": [lead["id"]]})
    assert resp.status_code == 400
    assert "criteria configured" in resp.json()["detail"]


def test_missing_research_is_flagged_and_generic_fallback_used(client, monkeypatch):
    monkeypatch.setattr(draft_service.draft_generator, "generate_draft", fake_generator())
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)

    draft = client.post(f"/campaigns/{campaign_id}/drafts/generate", json={"lead_ids": [lead["id"]]}).json()[0]

    flag_types = {f["type"] for f in draft["review_flags"]}
    assert "missing_research" in flag_types
    assert "generic_draft" in flag_types
    assert draft["is_generic_fallback"] is True
    assert draft["claim_sources"] == []


def test_completed_research_facts_are_used_and_cited(client, monkeypatch):
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    fact_ids = add_research_with_facts(
        get_db_session(), lead["id"], [{"category": "offering", "text": "Sells inventory software", "source_url": "https://acme.example/", "excerpt": "Sells inventory software"}]
    )

    monkeypatch.setattr(
        draft_service.draft_generator,
        "generate_draft",
        fake_generator(body="They sell inventory software.", claims=[{"claim": "They sell inventory software", "fact_id": fact_ids[0]}]),
    )

    draft = client.post(f"/campaigns/{campaign_id}/drafts/generate", json={"lead_ids": [lead["id"]]}).json()[0]

    assert draft["is_generic_fallback"] is False
    assert len(draft["claim_sources"]) == 1
    assert draft["claim_sources"][0]["fact_id"] == fact_ids[0]
    assert draft["claim_sources"][0]["source_url"] == "https://acme.example/"
    assert not any(f["type"] == "missing_research" for f in draft["review_flags"])


def test_expired_research_is_flagged_but_still_used(client, monkeypatch):
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    add_research_with_facts(
        get_db_session(),
        lead["id"],
        [{"category": "offering", "text": "Sells inventory software"}],
        expires_in_seconds=-3600,  # already expired
    )
    monkeypatch.setattr(draft_service.draft_generator, "generate_draft", fake_generator())

    draft = client.post(f"/campaigns/{campaign_id}/drafts/generate", json={"lead_ids": [lead["id"]]}).json()[0]

    flag_types = {f["type"] for f in draft["review_flags"]}
    assert "expired_research" in flag_types
    assert draft["is_generic_fallback"] is False  # still used despite being expired


def test_conflicting_research_facts_are_excluded_and_flagged(client, monkeypatch):
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    fact_ids = add_research_with_facts(
        get_db_session(),
        lead["id"],
        [
            {"category": "fact", "key": "headquarters", "text": "HQ in Skopje", "conflicting": True},
            {"category": "fact", "key": "headquarters", "text": "HQ in Berlin", "conflicting": True},
            {"category": "offering", "text": "Sells widgets", "conflicting": False},
        ],
        has_conflicts=True,
    )
    captured_facts = {}

    def capturing_generator(*, company, contact_name, product_service, facts, language, tone, length, call_model=None):
        captured_facts["facts"] = facts
        return GeneratedDraftResult("S", "B", [], "gpt-4o-mini", {})

    monkeypatch.setattr(draft_service.draft_generator, "generate_draft", capturing_generator)

    draft = client.post(f"/campaigns/{campaign_id}/drafts/generate", json={"lead_ids": [lead["id"]]}).json()[0]

    flag_types = {f["type"] for f in draft["review_flags"]}
    assert "conflicting_research_evidence" in flag_types
    # only the non-conflicting fact should have been shown to the generator
    assert len(captured_facts["facts"]) == 1
    assert captured_facts["facts"][0]["text"] == "Sells widgets"


def test_missing_qualification_is_flagged(client, monkeypatch):
    monkeypatch.setattr(draft_service.draft_generator, "generate_draft", fake_generator())
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)

    draft = client.post(f"/campaigns/{campaign_id}/drafts/generate", json={"lead_ids": [lead["id"]]}).json()[0]
    assert any(f["type"] == "missing_qualification" for f in draft["review_flags"])


def test_exclusions_are_surfaced_prominently_in_review_flags(client, monkeypatch):
    monkeypatch.setattr(draft_service.draft_generator, "generate_draft", fake_generator())
    campaign_id = create_campaign(client, exclusion_criteria=["Macedonia"])
    lead = import_lead(client, campaign_id, country="Macedonia")
    client.post(f"/leads/{lead['id']}/qualify")

    draft = client.post(f"/campaigns/{campaign_id}/drafts/generate", json={"lead_ids": [lead["id"]]}).json()[0]

    exclusion_flags = [f for f in draft["review_flags"] if f["type"] == "exclusion_triggered"]
    assert len(exclusion_flags) == 1
    assert len(exclusion_flags[0]["exclusions"]) == 1


def test_stale_qualification_is_flagged(client, monkeypatch):
    monkeypatch.setattr(draft_service.draft_generator, "generate_draft", fake_generator())
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id, industry="IT")
    client.post(f"/campaigns/{campaign_id}/criteria", json={"target_industries": ["IT"], "product_service": "X"})
    client.post(f"/leads/{lead['id']}/qualify")

    # New criteria version -> qualification is now stale relative to current criteria
    client.post(f"/campaigns/{campaign_id}/criteria", json={"target_industries": ["Finance"], "product_service": "X"})

    draft = client.post(f"/campaigns/{campaign_id}/drafts/generate", json={"lead_ids": [lead["id"]]}).json()[0]
    assert any(f["type"] == "stale_qualification" for f in draft["review_flags"])


# --- invalid/unsupported claims are filtered, never trusted at face value -----

def test_claim_referencing_fact_id_not_in_snapshot_is_dropped_and_flagged(client, monkeypatch):
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    add_research_with_facts(get_db_session(), lead["id"], [{"category": "offering", "text": "Sells widgets"}])

    monkeypatch.setattr(
        draft_service.draft_generator,
        "generate_draft",
        fake_generator(body="They raised $10M!", claims=[{"claim": "They raised $10M", "fact_id": 99999}]),
    )

    draft = client.post(f"/campaigns/{campaign_id}/drafts/generate", json={"lead_ids": [lead["id"]]}).json()[0]

    assert draft["claim_sources"] == []
    assert any(f["type"] == "invalid_fact_reference" for f in draft["review_flags"])


def test_unsupported_inference_in_body_is_flagged_even_with_no_claims(client, monkeypatch):
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    add_research_with_facts(get_db_session(), lead["id"], [{"category": "offering", "text": "Sells widgets"}])

    monkeypatch.setattr(
        draft_service.draft_generator,
        "generate_draft",
        fake_generator(body="Congrats on recently hired staff and your growing team!"),
    )

    draft = client.post(f"/campaigns/{campaign_id}/drafts/generate", json={"lead_ids": [lead["id"]]}).json()[0]
    assert any(f["type"] == "unsupported_inference" for f in draft["review_flags"])


# --- provider failure -----------------------------------------------------------

def test_provider_failure_after_bounded_retries_surfaces_as_400(client, monkeypatch):
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)

    def always_fails(**kwargs):
        raise DraftGenerationError("provider is down")

    monkeypatch.setattr(draft_service.draft_generator, "generate_draft", always_fails)

    resp = client.post(f"/campaigns/{campaign_id}/drafts/generate", json={"lead_ids": [lead["id"]]})
    assert resp.status_code == 400
    assert "provider is down" in resp.json()["detail"]


# --- language / tone / length validation ---------------------------------------

def test_invalid_language_is_rejected(client):
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    resp = client.post(f"/campaigns/{campaign_id}/drafts/generate", json={"lead_ids": [lead["id"]], "language": "fr"})
    assert resp.status_code == 400


def test_invalid_tone_is_rejected(client):
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    resp = client.post(f"/campaigns/{campaign_id}/drafts/generate", json={"lead_ids": [lead["id"]], "tone": "sarcastic"})
    assert resp.status_code == 400


def test_invalid_length_is_rejected(client):
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    resp = client.post(f"/campaigns/{campaign_id}/drafts/generate", json={"lead_ids": [lead["id"]], "length": "novel"})
    assert resp.status_code == 400


def test_valid_language_and_tone_are_persisted_on_the_version(client, monkeypatch):
    monkeypatch.setattr(draft_service.draft_generator, "generate_draft", fake_generator())
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)

    draft = client.post(
        f"/campaigns/{campaign_id}/drafts/generate",
        json={"lead_ids": [lead["id"]], "language": "mk", "tone": "friendly", "length": "short"},
    ).json()[0]

    assert draft["language"] == "mk"
    assert draft["tone"] == "friendly"
    assert draft["length"] == "short"


# --- token usage and metadata recorded ------------------------------------------

def test_token_usage_and_metadata_are_recorded(client, monkeypatch):
    monkeypatch.setattr(
        draft_service.draft_generator,
        "generate_draft",
        fake_generator(usage={"prompt_tokens": 111, "completion_tokens": 22, "total_tokens": 133}),
    )
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    draft_id = client.post(f"/campaigns/{campaign_id}/drafts/generate", json={"lead_ids": [lead["id"]]}).json()[0]["id"]

    history = client.get(f"/drafts/{draft_id}/history").json()
    assert history[0]["prompt_tokens"] == 111
    assert history[0]["completion_tokens"] == 22
    assert history[0]["total_tokens"] == 133
    assert history[0]["prompt_version"] == draft_service.PROMPT_VERSION
    assert history[0]["model"] == "gpt-4o-mini"
    assert history[0]["criteria_version_snapshot"] == 1


# --- draft history is preserved, never overwritten ------------------------------

def test_regenerating_preserves_all_previous_versions(client, monkeypatch):
    monkeypatch.setattr(draft_service.draft_generator, "generate_draft", fake_generator(subject="V1"))
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    draft = client.post(f"/campaigns/{campaign_id}/drafts/generate", json={"lead_ids": [lead["id"]]}).json()[0]

    monkeypatch.setattr(draft_service.draft_generator, "generate_draft", fake_generator(subject="V2"))
    client.post(f"/campaigns/{campaign_id}/drafts/generate", json={"lead_ids": [lead["id"]]})

    client.patch(f"/drafts/{draft['id']}", json={"subject": "V3 manual", "body": "manual body"})

    history = client.get(f"/drafts/{draft['id']}/history").json()
    assert [h["version_number"] for h in history] == [1, 2, 3]
    assert history[0]["subject"] == "V1"
    assert history[1]["subject"] == "V2"
    assert history[2]["subject"] == "V3 manual"
    assert history[2]["edited_manually"] is True
    assert history[0]["edited_manually"] is False


def test_manual_edit_flags_that_claims_were_not_reverified(client, monkeypatch):
    monkeypatch.setattr(draft_service.draft_generator, "generate_draft", fake_generator())
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    draft = client.post(f"/campaigns/{campaign_id}/drafts/generate", json={"lead_ids": [lead["id"]]}).json()[0]

    edited = client.patch(f"/drafts/{draft['id']}", json={"subject": "edited", "body": "edited body"}).json()
    assert edited["claim_sources"] == []
    assert any(f["type"] == "manually_edited" for f in edited["review_flags"])


# --- approval binding: exact subject, body, recipient, and version -------------

def test_approval_is_invalidated_by_editing_even_a_single_character(client, monkeypatch):
    monkeypatch.setattr(draft_service.draft_generator, "generate_draft", fake_generator(body="Original body."))
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    draft = client.post(f"/campaigns/{campaign_id}/drafts/generate", json={"lead_ids": [lead["id"]]}).json()[0]
    client.post(f"/drafts/{draft['id']}/approve")

    edited = client.patch(f"/drafts/{draft['id']}", json={"subject": draft["subject"], "body": "Original body!"}).json()
    assert edited["is_approved_for_send"] is False
    assert edited["approval_status"] == "pending"


def test_generation_never_calls_email_sender(client, monkeypatch):
    from modules import email_sender

    send_calls = []
    monkeypatch.setattr(email_sender, "send_email", lambda *a, **k: send_calls.append(a) or True)
    monkeypatch.setattr(draft_service.draft_generator, "generate_draft", fake_generator())

    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    client.post(f"/campaigns/{campaign_id}/drafts/generate", json={"lead_ids": [lead["id"]]})

    assert send_calls == []
