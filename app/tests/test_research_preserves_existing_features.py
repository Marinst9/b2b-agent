"""Regression coverage: adding company research must not disturb the
existing campaigns/CSV-import/draft/approval/send milestone."""
from modules import web_fetcher
from modules.research_extractor import ExtractionResult, ExtractedFact
import research_service


def create_campaign(client, name="Preservation Co"):
    campaign_id = client.post("/campaigns", json={"name": name}).json()["id"]
    client.post(f"/campaigns/{campaign_id}/criteria", json={"product_service": "B2B outreach automation"})
    return campaign_id


def import_lead(client, campaign_id, website="acme.example", email="lead@co.example"):
    csv_bytes = (
        f"name,company,industry,country,email,website\n"
        f"Lead Person,Acme,IT,Macedonia,{email},{website}\n"
    ).encode()
    client.post(
        f"/campaigns/{campaign_id}/leads/import",
        files={"file": ("leads.csv", csv_bytes, "text/csv")},
    )
    return client.get(f"/campaigns/{campaign_id}/leads").json()[0]


def test_full_campaign_lifecycle_unaffected_by_research_module_presence(client, monkeypatch):
    """Import -> draft -> approve -> send (dry-run) still works exactly as
    before, and running research alongside it changes nothing about that
    flow."""
    monkeypatch.setattr(
        research_service.web_fetcher,
        "fetch_url",
        lambda url, **k: type("P", (), {"url": url, "status_code": 200, "content_type": "text/html", "text": "<html><body>Acme sells widgets.</body></html>"})(),
    )
    monkeypatch.setattr(
        research_service.research_extractor,
        "extract_facts",
        lambda text, url, **k: ExtractionResult(
            facts=[ExtractedFact(category="offering", text="Sells widgets", excerpt="sells widgets", source_url=url)]
        ),
    )

    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)

    draft = client.post(f"/campaigns/{campaign_id}/drafts/generate").json()[0]
    assert draft["version"] == 1
    approved = client.post(f"/drafts/{draft['id']}/approve").json()
    assert approved["is_approved_for_send"] is True

    # Run research for the same lead in between -- must not touch the draft.
    research = client.post(f"/leads/{lead['id']}/research").json()
    assert research["status"] == "completed"

    still_approved = client.get(f"/campaigns/{campaign_id}/drafts").json()[0]
    assert still_approved["approval_status"] == "approved"
    assert still_approved["version"] == 1
    assert still_approved["is_approved_for_send"] is True

    send_result = client.post(f"/campaigns/{campaign_id}/send", json={"dry_run": True}).json()
    assert send_result["sent"] == 1

    leads = client.get(f"/campaigns/{campaign_id}/leads").json()
    assert leads[0]["status"] == "dry_run_sent"


def test_stats_endpoint_unaffected_by_research_data(client, monkeypatch):
    monkeypatch.setattr(research_service.web_fetcher, "fetch_url", lambda url, **k: (_ for _ in ()).throw(web_fetcher.FetchError("down")))

    campaign_id = create_campaign(client)
    import_lead(client, campaign_id)
    client.post(f"/leads/{client.get(f'/campaigns/{campaign_id}/leads').json()[0]['id']}/research")

    stats = client.get(f"/stats?campaign_id={campaign_id}").json()
    assert stats["total_leads"] == 1
    assert stats["opened"] is None
    assert stats["tracking_available"] == {"opened": False, "replied": False}


def test_run_endpoint_still_generates_drafts_without_deleting_leads_or_research(client, monkeypatch):
    monkeypatch.setattr(
        research_service.web_fetcher,
        "fetch_url",
        lambda url, **k: type("P", (), {"url": url, "status_code": 200, "content_type": "text/html", "text": "hi"})(),
    )
    monkeypatch.setattr(research_service.research_extractor, "extract_facts", lambda text, url, **k: ExtractionResult(facts=[]))

    campaign_id = create_campaign(client)
    lead = import_lead(client, campaign_id)
    client.post(f"/leads/{lead['id']}/research")

    resp = client.post(f"/run?campaign_id={campaign_id}")
    assert resp.status_code == 200

    leads = client.get(f"/campaigns/{campaign_id}/leads").json()
    assert len(leads) == 1  # nothing deleted
    research_after = client.get(f"/leads/{lead['id']}/research").json()
    assert research_after["status"] == "completed"  # research untouched by /run
