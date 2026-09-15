import database
from evaluation.dataset import load_dataset
from evaluation import harness, store


def _seed_run(mode="mock"):
    dataset = load_dataset()
    outcomes = harness.run(dataset, mode=mode, case_ids=["eval-001", "eval-020"])
    db = database.SessionLocal()
    try:
        run = store.save_run(db, dataset, outcomes, mode=mode, language="en", tone="professional", length="medium")
        return run.id
    finally:
        db.close()


def test_get_evaluation_dataset(client):
    resp = client.get("/evaluation/dataset")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == "companies_v1"
    assert 20 <= body["case_count"] <= 30
    assert all(c["synthetic"] is True for c in body["cases"])


def test_list_evaluation_runs_empty_then_after_seeding(client):
    assert client.get("/evaluation/runs").json() == []
    run_id = _seed_run()
    runs = client.get("/evaluation/runs").json()
    assert len(runs) == 1
    assert runs[0]["id"] == run_id
    assert runs[0]["mode"] == "mock"
    assert runs[0]["result_count"] == 4  # 2 cases x 2 approaches


def test_get_run_results_returns_not_evaluated_by_default(client):
    run_id = _seed_run()
    body = client.get(f"/evaluation/runs/{run_id}/results").json()
    assert body["run"]["id"] == run_id
    assert len(body["results"]) == 4
    for r in body["results"]:
        assert r["human_rating_relevance"] is None
        assert r["human_rating_personalization"] is None
        assert r["evaluated"] is False


def test_get_run_results_404_for_unknown_run(client):
    resp = client.get("/evaluation/runs/999999/results")
    assert resp.status_code == 404


def test_submit_rating_marks_result_evaluated(client):
    run_id = _seed_run()
    results = client.get(f"/evaluation/runs/{run_id}/results").json()["results"]
    result_id = results[0]["id"]

    resp = client.post(f"/evaluation/results/{result_id}/rating", json={"relevance": 4, "personalization": 5, "notes": "Good use of the fact."})
    assert resp.status_code == 200
    body = resp.json()
    assert body["human_rating_relevance"] == 4
    assert body["human_rating_personalization"] == 5
    assert body["human_notes"] == "Good use of the fact."
    assert body["evaluated"] is True
    assert body["human_rated_at"] is not None


def test_submit_rating_404_for_unknown_result(client):
    resp = client.post("/evaluation/results/999999/rating", json={"relevance": 3})
    assert resp.status_code == 404


def test_submit_rating_rejects_out_of_range_score(client):
    run_id = _seed_run()
    result_id = client.get(f"/evaluation/runs/{run_id}/results").json()["results"][0]["id"]
    resp = client.post(f"/evaluation/results/{result_id}/rating", json={"relevance": 9})
    assert resp.status_code == 422
