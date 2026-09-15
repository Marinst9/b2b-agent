import qualification_service as qs


def create_campaign(client, name="Qualification Co"):
    return client.post("/campaigns", json={"name": name}).json()["id"]


def import_lead(client, campaign_id, industry="IT", country="Macedonia", website="", email="lead@co.example"):
    csv_bytes = (
        f"name,company,industry,country,email,website\n"
        f"Lead Person,Acme,{industry},{country},{email},{website}\n"
    ).encode()
    client.post(
        f"/campaigns/{campaign_id}/leads/import",
        files={"file": ("leads.csv", csv_bytes, "text/csv")},
    )
    return client.get(f"/campaigns/{campaign_id}/leads").json()[0]


def set_criteria(client, campaign_id, **kwargs):
    return client.post(f"/campaigns/{campaign_id}/criteria", json=kwargs).json()


# --- criteria versioning --------------------------------------------------------

def test_creating_criteria_starts_at_version_1_and_increments(client):
    campaign_id = create_campaign(client)
    v1 = set_criteria(client, campaign_id, target_industries=["IT"])
    v2 = set_criteria(client, campaign_id, target_industries=["IT", "Finance"])

    assert v1["version"] == 1
    assert v2["version"] == 2

    history = client.get(f"/campaigns/{campaign_id}/criteria").json()
    assert [c["version"] for c in history] == [1, 2]


def test_qualify_without_criteria_configured_is_rejected(client):
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    resp = client.post(f"/leads/{lead['id']}/qualify")
    assert resp.status_code == 400


# --- qualify + explainability fields exposed via API ---------------------------

def test_qualify_lead_returns_explainable_breakdown(client):
    campaign_id = create_campaign(client)
    set_criteria(client, campaign_id, target_industries=["IT"], target_countries=["Macedonia"])
    lead = import_lead(client, campaign_id, industry="IT", country="Macedonia")

    resp = client.post(f"/leads/{lead['id']}/qualify")
    body = resp.json()

    assert body["fit_score"] == 100.0
    assert body["evidence_coverage"] == 1.0
    assert body["rubric_version"] == qs.RUBRIC_VERSION
    assert body["criteria_version"] == 1
    assert len(body["matched"]) == 2
    assert body["unmatched"] == []
    assert body["unknown"] == []
    assert body["exclusions"] == []
    assert body["is_stale"] is False


def test_qualification_never_describes_score_as_probability(client):
    """Guard against regressions in field naming: no field name should imply
    a conversion probability."""
    campaign_id = create_campaign(client)
    set_criteria(client, campaign_id, target_industries=["IT"])
    lead = import_lead(client, campaign_id, industry="IT")
    body = client.post(f"/leads/{lead['id']}/qualify").json()

    banned_terms = {"probability", "conversion", "likelihood", "chance"}
    for key in body.keys():
        assert not any(term in key.lower() for term in banned_terms)


# --- staleness: criteria change -------------------------------------------------

def test_qualification_becomes_stale_when_criteria_change(client):
    campaign_id = create_campaign(client)
    set_criteria(client, campaign_id, target_industries=["IT"])
    lead = import_lead(client, campaign_id, industry="IT")

    result = client.post(f"/leads/{lead['id']}/qualify").json()
    assert result["is_stale"] is False

    set_criteria(client, campaign_id, target_industries=["Finance"])  # new version, not re-qualified yet

    stale_check = client.get(f"/leads/{lead['id']}/qualification").json()
    assert stale_check["is_stale"] is True
    assert stale_check["criteria_version"] == 1  # still reflects the version it was actually computed against

    refreshed = client.post(f"/leads/{lead['id']}/qualify").json()
    assert refreshed["is_stale"] is False
    assert refreshed["criteria_version"] == 2


# --- staleness: research changes -------------------------------------------------

def test_qualification_becomes_stale_when_research_is_refreshed(client, monkeypatch):
    import research_service
    from modules.research_extractor import ExtractionResult, ExtractedFact

    page = type("P", (), {"url": "https://acme.example/", "status_code": 200, "content_type": "text/html", "text": "hi"})()
    monkeypatch.setattr(research_service.web_fetcher, "fetch_url", lambda url, **k: page)
    monkeypatch.setattr(research_service.research_extractor, "extract_facts", lambda t, u, **k: ExtractionResult(facts=[]))

    campaign_id = create_campaign(client)
    set_criteria(client, campaign_id, target_industries=["IT"])
    lead = import_lead(client, campaign_id, industry="IT", website="acme.example")

    client.post(f"/leads/{lead['id']}/research")
    result = client.post(f"/leads/{lead['id']}/qualify").json()
    assert result["is_stale"] is False
    assert result["research_version_snapshot"] == 1

    client.post(f"/leads/{lead['id']}/research", json={"force_refresh": True})  # research version bumps to 2

    stale_check = client.get(f"/leads/{lead['id']}/qualification").json()
    assert stale_check["is_stale"] is True


# --- bulk campaign qualify + listing for sort/filter ---------------------------

def test_bulk_qualify_campaign_and_list_endpoint(client):
    campaign_id = create_campaign(client)
    set_criteria(client, campaign_id, target_industries=["IT"])
    csv_bytes = (
        "name,company,industry,country,email,website\n"
        "A,CoA,IT,Macedonia,a@co.mk,\n"
        "B,CoB,Finance,Macedonia,b@co.mk,\n"
    ).encode()
    client.post(f"/campaigns/{campaign_id}/leads/import", files={"file": ("leads.csv", csv_bytes, "text/csv")})

    results = client.post(f"/campaigns/{campaign_id}/qualify").json()
    assert len(results) == 2

    listing = client.get(f"/campaigns/{campaign_id}/qualifications").json()
    scores = {row["company"]: row["qualification"]["fit_score"] for row in listing}
    assert scores["CoA"] == 100.0
    assert scores["CoB"] == 0.0


# --- manual labels: separate from automated score, versions captured ----------

def test_add_label_captures_notes_timestamp_and_versions(client):
    campaign_id = create_campaign(client)
    set_criteria(client, campaign_id, target_industries=["IT"])
    lead = import_lead(client, campaign_id, industry="IT")
    client.post(f"/leads/{lead['id']}/qualify")

    resp = client.post(f"/leads/{lead['id']}/qualification/labels", json={"label": "suitable", "notes": "Looks promising"})
    body = resp.json()

    assert body["label"] == "suitable"
    assert body["notes"] == "Looks promising"
    assert body["criteria_version_reviewed"] == 1
    assert body["research_version_reviewed"] == 0
    assert body["labeled_at"] is not None


def test_invalid_label_value_rejected(client):
    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    resp = client.post(f"/leads/{lead['id']}/qualification/labels", json={"label": "maybe-ish"})
    assert resp.status_code == 400


def test_label_history_is_append_only_and_independent_of_score(client):
    campaign_id = create_campaign(client)
    set_criteria(client, campaign_id, target_industries=["IT"])
    lead = import_lead(client, campaign_id, industry="IT")

    client.post(f"/leads/{lead['id']}/qualification/labels", json={"label": "unsure"})
    client.post(f"/leads/{lead['id']}/qualification/labels", json={"label": "suitable", "notes": "confirmed after call"})

    history = client.get(f"/leads/{lead['id']}/qualification/labels").json()
    assert [h["label"] for h in history] == ["unsure", "suitable"]

    # Re-running the deterministic scorer must not touch label history at all.
    client.post(f"/leads/{lead['id']}/qualify")
    history_after = client.get(f"/leads/{lead['id']}/qualification/labels").json()
    assert history_after == history


def test_labeling_does_not_change_qualification_score(client):
    campaign_id = create_campaign(client)
    set_criteria(client, campaign_id, target_industries=["IT"])
    lead = import_lead(client, campaign_id, industry="IT")
    before = client.post(f"/leads/{lead['id']}/qualify").json()

    client.post(f"/leads/{lead['id']}/qualification/labels", json={"label": "unsuitable"})

    after = client.get(f"/leads/{lead['id']}/qualification").json()
    assert after["fit_score"] == before["fit_score"]
    assert after["computed_at"] == before["computed_at"]
