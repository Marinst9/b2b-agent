"""API-level tests for the /jobs endpoints. Since this environment has no
real Redis/Celery broker, job creation here always exercises the durable
dispatch (outbox) path -- dispatch to the broker fails, and the job is
correctly persisted anyway with dispatched=False. That failure path IS the
thing under test here; see tests/integration/README.md for what requires a
real broker and worker instead.
"""


def create_campaign(client, name="Job API Co"):
    campaign_id = client.post("/campaigns", json={"name": name}).json()["id"]
    client.post(f"/campaigns/{campaign_id}/criteria", json={"product_service": "B2B analytics"})
    return campaign_id


def import_leads(client, campaign_id, n=2):
    rows = [f"L{i},Co{i},IT,Macedonia,l{i}@co.example," for i in range(n)]
    csv_bytes = ("name,company,industry,country,email,website\n" + "\n".join(rows)).encode()
    client.post(f"/campaigns/{campaign_id}/leads/import", files={"file": ("leads.csv", csv_bytes, "text/csv")})
    return [l["id"] for l in client.get(f"/campaigns/{campaign_id}/leads").json()]


def test_create_workflow_job_returns_202_immediately(client):
    campaign_id = create_campaign(client)
    import_leads(client, campaign_id, 1)

    resp = client.post(f"/campaigns/{campaign_id}/jobs/workflow")
    assert resp.status_code == 202
    body = resp.json()
    assert body["job_type"] == "workflow"
    assert body["status"] in ("queued", "running")
    assert len(body["steps"]) == 3  # research, qualify, draft for the one lead


def test_create_job_without_lead_ids_targets_all_campaign_leads(client):
    campaign_id = create_campaign(client)
    import_leads(client, campaign_id, 3)

    resp = client.post(f"/campaigns/{campaign_id}/jobs/research")
    body = resp.json()
    assert len(body["steps"]) == 3


def test_create_job_with_explicit_lead_ids(client):
    campaign_id = create_campaign(client)
    lead_ids = import_leads(client, campaign_id, 3)

    resp = client.post(f"/campaigns/{campaign_id}/jobs/research", json={"lead_ids": lead_ids[:2]})
    body = resp.json()
    assert len(body["steps"]) == 2


def test_get_job_returns_current_status(client):
    campaign_id = create_campaign(client)
    import_leads(client, campaign_id, 1)
    job_id = client.post(f"/campaigns/{campaign_id}/jobs/research").json()["id"]

    resp = client.get(f"/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == job_id


def test_get_nonexistent_job_is_400(client):
    resp = client.get("/jobs/999999")
    assert resp.status_code == 400


def test_list_campaign_jobs(client):
    campaign_id = create_campaign(client)
    import_leads(client, campaign_id, 1)
    client.post(f"/campaigns/{campaign_id}/jobs/research")
    client.post(f"/campaigns/{campaign_id}/jobs/qualify")

    jobs = client.get(f"/campaigns/{campaign_id}/jobs").json()
    assert len(jobs) == 2


def test_cancel_job(client):
    campaign_id = create_campaign(client)
    import_leads(client, campaign_id, 1)
    job_id = client.post(f"/campaigns/{campaign_id}/jobs/research").json()["id"]

    resp = client.post(f"/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


def test_cannot_cancel_terminal_job_twice(client):
    campaign_id = create_campaign(client)
    import_leads(client, campaign_id, 1)
    job_id = client.post(f"/campaigns/{campaign_id}/jobs/research").json()["id"]
    client.post(f"/jobs/{job_id}/cancel")

    resp = client.post(f"/jobs/{job_id}/cancel")
    assert resp.status_code == 400


def test_job_stuck_undispatched_without_a_broker_is_still_visible_via_the_api(client):
    """No real broker is running in this environment, so job creation here
    always exercises the "broker unreachable" branch of durable dispatch --
    the job must still be fully visible and inspectable via the API rather
    than disappearing."""
    campaign_id = create_campaign(client)
    import_leads(client, campaign_id, 1)
    body = client.post(f"/campaigns/{campaign_id}/jobs/research").json()

    assert body["dispatched"] is False
    assert body["dispatch_attempts"] >= 1
    assert body["status"] == "queued"

    refetched = client.get(f"/jobs/{body['id']}").json()
    assert refetched["id"] == body["id"]
    assert refetched["steps"][0]["status"] == "queued"


def test_retry_job_is_distinct_from_creating_a_new_job(client):
    campaign_id = create_campaign(client)
    import_leads(client, campaign_id, 1)
    job_id = client.post(f"/campaigns/{campaign_id}/jobs/research").json()["id"]
    client.post(f"/jobs/{job_id}/cancel")

    resp = client.post(f"/jobs/{job_id}/retry")
    assert resp.status_code == 200
    assert resp.json()["id"] == job_id  # same job, not a new one

    jobs = client.get(f"/campaigns/{campaign_id}/jobs").json()
    assert len(jobs) == 1  # retry did not create an additional job row
