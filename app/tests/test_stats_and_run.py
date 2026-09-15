def create_campaign(client, name="Q1 Outreach"):
    campaign_id = client.post("/campaigns", json={"name": name}).json()["id"]
    client.post(f"/campaigns/{campaign_id}/criteria", json={"product_service": "B2B outreach automation"})
    return campaign_id


def import_leads(client, campaign_id, rows):
    header = "name,company,industry,country,email,website\n"
    csv_bytes = (header + "\n".join(rows)).encode()
    return client.post(
        f"/campaigns/{campaign_id}/leads/import",
        files={"file": ("leads.csv", csv_bytes, "text/csv")},
    ).json()


def test_stats_reflect_real_database_state_not_hardcoded_values(client):
    campaign_id = create_campaign(client)
    import_leads(
        client,
        campaign_id,
        [
            "A,CoA,IT,Macedonia,a@co.mk,",
            "B,CoB,Finance,Macedonia,b@co.mk,",
        ],
    )

    stats = client.get("/stats").json()
    assert stats["total_leads"] == 2
    assert stats["by_industry"] == {"IT": 1, "Finance": 1}

    drafts = client.post(f"/campaigns/{campaign_id}/drafts/generate").json()
    stats = client.get("/stats").json()
    assert stats["drafted"] == 2

    client.post(f"/drafts/{drafts[0]['id']}/approve")
    stats = client.get("/stats").json()
    assert stats["approved"] == 1
    assert stats["drafted"] == 1  # the other lead is still only drafted

    client.post(f"/campaigns/{campaign_id}/send", json={"dry_run": True})
    stats = client.get("/stats").json()
    assert stats["dry_run_sent"] == 1


def test_stats_mark_open_and_reply_tracking_as_unavailable(client):
    stats = client.get("/stats").json()
    assert stats["opened"] is None
    assert stats["replied"] is None
    assert stats["tracking_available"] == {"opened": False, "replied": False}


def test_stats_can_be_scoped_to_a_single_campaign(client):
    campaign_a = create_campaign(client, "A")
    campaign_b = create_campaign(client, "B")
    import_leads(client, campaign_a, ["Person,Co,IT,Macedonia,p@co.mk,"])
    import_leads(client, campaign_b, ["P1,Co,IT,Macedonia,p1@co.mk,", "P2,Co,IT,Macedonia,p2@co.mk,"])

    stats_a = client.get(f"/stats?campaign_id={campaign_a}").json()
    stats_b = client.get(f"/stats?campaign_id={campaign_b}").json()

    assert stats_a["total_leads"] == 1
    assert stats_b["total_leads"] == 2


def test_run_endpoint_generates_drafts_without_deleting_leads(client):
    campaign_id = create_campaign(client)
    import_leads(
        client,
        campaign_id,
        ["A,CoA,IT,Macedonia,a@co.mk,", "B,CoB,IT,Macedonia,b@co.mk,"],
    )

    before = client.get(f"/campaigns/{campaign_id}/leads").json()
    assert len(before) == 2

    resp = client.post(f"/run?campaign_id={campaign_id}")
    assert resp.status_code == 200

    after = client.get(f"/campaigns/{campaign_id}/leads").json()
    assert len(after) == 2  # nothing was deleted
    assert all(l["draft"] is not None for l in after)


def test_run_does_not_send_any_email(client, monkeypatch):
    from modules import email_sender

    called = []
    monkeypatch.setattr(email_sender, "send_email", lambda *a, **k: called.append(a) or True)

    campaign_id = create_campaign(client)
    import_leads(client, campaign_id, ["A,CoA,IT,Macedonia,a@co.mk,"])
    client.post(f"/run?campaign_id={campaign_id}")

    assert called == []
