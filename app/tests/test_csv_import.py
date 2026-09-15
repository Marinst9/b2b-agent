import io

from modules.csv_handler import MAX_FILE_SIZE_BYTES, MAX_ROWS


def make_csv(rows, headers="name,company,industry,country,email,website"):
    lines = [headers]
    lines.extend(rows)
    return ("\n".join(lines)).encode("utf-8")


def create_campaign(client, name="Q1 Outreach"):
    resp = client.post("/campaigns", json={"name": name})
    assert resp.status_code == 200
    return resp.json()["id"]


def test_import_valid_rows(client):
    campaign_id = create_campaign(client)
    csv_bytes = make_csv(
        [
            "Marko Petrovski,TechSoft,IT,Macedonia,marko@techsoft.mk,techsoft.mk",
            "Ana Jovevska,FinApp,Finance,Macedonia,,finapp.mk",
        ]
    )
    resp = client.post(
        f"/campaigns/{campaign_id}/leads/import",
        files={"file": ("leads.csv", csv_bytes, "text/csv")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["imported"] == 2
    assert body["duplicates"] == 0
    assert body["row_errors"] == []

    leads = client.get(f"/campaigns/{campaign_id}/leads").json()
    assert len(leads) == 2


def test_missing_required_headers_rejected(client):
    campaign_id = create_campaign(client)
    csv_bytes = b"industry,country\nIT,Macedonia\n"
    resp = client.post(
        f"/campaigns/{campaign_id}/leads/import",
        files={"file": ("leads.csv", csv_bytes, "text/csv")},
    )
    assert resp.status_code == 400
    assert "company" in resp.json()["detail"]
    assert "name" in resp.json()["detail"]


def test_row_level_errors_reported_and_valid_rows_still_imported(client):
    campaign_id = create_campaign(client)
    csv_bytes = make_csv(
        [
            ",TechSoft,IT,Macedonia,marko@techsoft.mk,",  # missing name
            "Ana Jovevska,,Finance,Macedonia,ana@finapp.mk,",  # missing company
            "Petar Stojanov,MarketPro,Marketing,Serbia,not-an-email,",  # bad email
            "Sara Nikolova,DevStudio,IT,Macedonia,sara@devstudio.mk,",  # valid
        ]
    )
    resp = client.post(
        f"/campaigns/{campaign_id}/leads/import",
        files={"file": ("leads.csv", csv_bytes, "text/csv")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["imported"] == 1
    assert len(body["row_errors"]) == 3
    fields = {e["field"] for e in body["row_errors"]}
    assert fields == {"name", "company", "email"}


def test_file_too_large_rejected(client):
    campaign_id = create_campaign(client)
    padding_row = "Name,Company,IT,Macedonia,,"
    huge_body = "name,company,industry,country,email,website\n" + (padding_row + "\n") * 1
    # Pad the file content itself (not just rows) past the byte limit.
    huge_bytes = huge_body.encode() + b"x" * (MAX_FILE_SIZE_BYTES + 1)
    resp = client.post(
        f"/campaigns/{campaign_id}/leads/import",
        files={"file": ("leads.csv", huge_bytes, "text/csv")},
    )
    assert resp.status_code == 400
    assert "too large" in resp.json()["detail"].lower()


def test_too_many_rows_rejected(client):
    campaign_id = create_campaign(client)
    rows = [f"Person {i},Company {i},IT,Macedonia,person{i}@co.mk," for i in range(MAX_ROWS + 1)]
    csv_bytes = make_csv(rows)
    resp = client.post(
        f"/campaigns/{campaign_id}/leads/import",
        files={"file": ("leads.csv", csv_bytes, "text/csv")},
    )
    assert resp.status_code == 400
    assert "too many rows" in resp.json()["detail"].lower()


def test_duplicate_rows_within_same_file_are_skipped(client):
    campaign_id = create_campaign(client)
    csv_bytes = make_csv(
        [
            "Marko Petrovski,TechSoft,IT,Macedonia,marko@techsoft.mk,",
            "Marko Petrovski,TechSoft,IT,Macedonia,marko@techsoft.mk,",
        ]
    )
    resp = client.post(
        f"/campaigns/{campaign_id}/leads/import",
        files={"file": ("leads.csv", csv_bytes, "text/csv")},
    )
    body = resp.json()
    assert body["imported"] == 1
    assert body["duplicates"] == 1


def test_duplicate_import_across_two_uploads_in_same_campaign(client):
    campaign_id = create_campaign(client)
    csv_bytes = make_csv(["Marko Petrovski,TechSoft,IT,Macedonia,marko@techsoft.mk,"])

    first = client.post(
        f"/campaigns/{campaign_id}/leads/import",
        files={"file": ("leads.csv", csv_bytes, "text/csv")},
    ).json()
    assert first["imported"] == 1

    second = client.post(
        f"/campaigns/{campaign_id}/leads/import",
        files={"file": ("leads.csv", csv_bytes, "text/csv")},
    ).json()
    assert second["imported"] == 0
    assert second["duplicates"] == 1

    leads = client.get(f"/campaigns/{campaign_id}/leads").json()
    assert len(leads) == 1


def test_same_lead_allowed_in_different_campaigns(client):
    campaign_a = create_campaign(client, "Campaign A")
    campaign_b = create_campaign(client, "Campaign B")
    csv_bytes = make_csv(["Marko Petrovski,TechSoft,IT,Macedonia,marko@techsoft.mk,"])

    resp_a = client.post(
        f"/campaigns/{campaign_a}/leads/import",
        files={"file": ("leads.csv", csv_bytes, "text/csv")},
    ).json()
    resp_b = client.post(
        f"/campaigns/{campaign_b}/leads/import",
        files={"file": ("leads.csv", csv_bytes, "text/csv")},
    ).json()

    assert resp_a["imported"] == 1
    assert resp_b["imported"] == 1


def test_import_uses_explicit_website_domain_for_enrichment(client, monkeypatch):
    """When a website is given, enrichment must use that exact domain -- never a
    guessed one like company-name + '.mk'."""
    from modules import enrichment

    calls = []

    def fake_get_email(first_name, last_name, domain):
        calls.append(domain)
        return {"email": "found@explicit-domain.com", "score": 99}

    monkeypatch.setattr(enrichment, "get_email", fake_get_email)

    campaign_id = create_campaign(client)
    csv_bytes = make_csv(["Marko Petrovski,TechSoft,IT,Macedonia,,https://explicit-domain.com/about"])
    client.post(
        f"/campaigns/{campaign_id}/leads/import",
        files={"file": ("leads.csv", csv_bytes, "text/csv")},
    )

    assert calls == ["explicit-domain.com"]
    leads = client.get(f"/campaigns/{campaign_id}/leads").json()
    assert leads[0]["email"] == "found@explicit-domain.com"


def test_import_never_guesses_domain_or_falls_back_to_test_example(client):
    """A lead with no email and no website must stay email=None -- never a
    guessed domain lookup and never a 'test@example.com' substitution."""
    campaign_id = create_campaign(client)
    csv_bytes = make_csv(["Marko Petrovski,TechSoft,IT,Macedonia,,"])
    client.post(
        f"/campaigns/{campaign_id}/leads/import",
        files={"file": ("leads.csv", csv_bytes, "text/csv")},
    )
    leads = client.get(f"/campaigns/{campaign_id}/leads").json()
    assert len(leads) == 1
    assert leads[0]["email"] is None
    assert leads[0]["website"] is None
