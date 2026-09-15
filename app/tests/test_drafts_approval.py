def create_campaign(client, name="Q1 Outreach"):
    campaign_id = client.post("/campaigns", json={"name": name}).json()["id"]
    # Draft generation requires campaign criteria (for the product/service
    # description), so every test in this file gets a minimal one by default.
    client.post(f"/campaigns/{campaign_id}/criteria", json={"product_service": "B2B outreach automation"})
    return campaign_id


def import_one_lead(client, campaign_id, email="marko@techsoft.mk"):
    csv_bytes = (
        f"name,company,industry,country,email,website\n"
        f"Marko Petrovski,TechSoft,IT,Macedonia,{email},\n"
    ).encode()
    client.post(
        f"/campaigns/{campaign_id}/leads/import",
        files={"file": ("leads.csv", csv_bytes, "text/csv")},
    )
    lead = client.get(f"/campaigns/{campaign_id}/leads").json()[0]
    return lead


def test_generate_draft_creates_pending_version_one(client):
    campaign_id = create_campaign(client)
    import_one_lead(client, campaign_id)

    drafts = client.post(f"/campaigns/{campaign_id}/drafts/generate").json()
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft["version"] == 1
    assert draft["approval_status"] == "pending"
    assert draft["is_approved_for_send"] is False


def test_approve_binds_to_exact_version_and_recipient(client):
    campaign_id = create_campaign(client)
    lead = import_one_lead(client, campaign_id)
    draft = client.post(f"/campaigns/{campaign_id}/drafts/generate").json()[0]

    approved = client.post(f"/drafts/{draft['id']}/approve").json()
    assert approved["approval_status"] == "approved"
    assert approved["approved_version"] == 1
    assert approved["approved_recipient_email"] == lead["email"]
    assert approved["is_approved_for_send"] is True


def test_editing_draft_invalidates_approval(client):
    campaign_id = create_campaign(client)
    import_one_lead(client, campaign_id)
    draft = client.post(f"/campaigns/{campaign_id}/drafts/generate").json()[0]
    client.post(f"/drafts/{draft['id']}/approve")

    edited = client.patch(
        f"/drafts/{draft['id']}",
        json={"subject": "New subject", "body": "New body"},
    ).json()

    assert edited["version"] == 2
    assert edited["approval_status"] == "pending"
    assert edited["is_approved_for_send"] is False


def test_regenerating_draft_invalidates_prior_approval(client):
    campaign_id = create_campaign(client)
    import_one_lead(client, campaign_id)
    draft = client.post(f"/campaigns/{campaign_id}/drafts/generate").json()[0]
    client.post(f"/drafts/{draft['id']}/approve")

    regenerated = client.post(f"/campaigns/{campaign_id}/drafts/generate").json()[0]
    assert regenerated["version"] == 2
    assert regenerated["approval_status"] == "pending"
    assert regenerated["is_approved_for_send"] is False


def test_reject_draft(client):
    campaign_id = create_campaign(client)
    import_one_lead(client, campaign_id)
    draft = client.post(f"/campaigns/{campaign_id}/drafts/generate").json()[0]

    rejected = client.post(f"/drafts/{draft['id']}/reject").json()
    assert rejected["approval_status"] == "rejected"
    assert rejected["is_approved_for_send"] is False


def test_cannot_approve_draft_with_no_recipient_email(client):
    campaign_id = create_campaign(client)
    csv_bytes = (
        b"name,company,industry,country,email,website\n"
        b"Marko Petrovski,TechSoft,IT,Macedonia,,\n"
    )
    client.post(
        f"/campaigns/{campaign_id}/leads/import",
        files={"file": ("leads.csv", csv_bytes, "text/csv")},
    )
    draft = client.post(f"/campaigns/{campaign_id}/drafts/generate").json()[0]

    resp = client.post(f"/drafts/{draft['id']}/approve")
    assert resp.status_code == 400


def test_send_defaults_to_dry_run_and_does_not_call_real_email_sender(client, monkeypatch):
    from modules import email_sender

    called = []
    monkeypatch.setattr(email_sender, "send_email", lambda *a, **k: called.append(a) or True)

    campaign_id = create_campaign(client)
    import_one_lead(client, campaign_id)
    draft = client.post(f"/campaigns/{campaign_id}/drafts/generate").json()[0]
    client.post(f"/drafts/{draft['id']}/approve")

    result = client.post(f"/campaigns/{campaign_id}/send").json()  # no body -> dry_run defaults True

    assert result["dry_run"] is True
    assert result["sent"] == 1
    assert called == []  # real sender was never invoked

    leads = client.get(f"/campaigns/{campaign_id}/leads").json()
    assert leads[0]["status"] == "dry_run_sent"


def test_send_skips_unapproved_leads(client):
    campaign_id = create_campaign(client)
    import_one_lead(client, campaign_id)
    client.post(f"/campaigns/{campaign_id}/drafts/generate")  # generated but not approved

    result = client.post(f"/campaigns/{campaign_id}/send", json={"dry_run": True}).json()
    assert result["sent"] == 0
    assert result["skipped"] == 1


def test_send_skips_lead_whose_approval_was_invalidated_by_edit(client):
    campaign_id = create_campaign(client)
    import_one_lead(client, campaign_id)
    draft = client.post(f"/campaigns/{campaign_id}/drafts/generate").json()[0]
    client.post(f"/drafts/{draft['id']}/approve")
    client.patch(f"/drafts/{draft['id']}", json={"subject": "changed", "body": "changed"})

    result = client.post(f"/campaigns/{campaign_id}/send", json={"dry_run": True}).json()
    assert result["sent"] == 0
    assert result["skipped"] == 1


def test_real_send_mode_calls_mocked_email_sender_only_for_approved_leads(client, monkeypatch):
    from modules import email_sender

    called = []
    monkeypatch.setattr(email_sender, "send_email", lambda *a, **k: called.append(a) or True)

    campaign_id = create_campaign(client)
    lead = import_one_lead(client, campaign_id)
    draft = client.post(f"/campaigns/{campaign_id}/drafts/generate").json()[0]
    client.post(f"/drafts/{draft['id']}/approve")

    result = client.post(f"/campaigns/{campaign_id}/send", json={"dry_run": False}).json()

    assert result["dry_run"] is False
    assert result["sent"] == 1
    assert len(called) == 1
    assert called[0][0] == lead["email"]

    leads = client.get(f"/campaigns/{campaign_id}/leads").json()
    assert leads[0]["status"] == "sent"
