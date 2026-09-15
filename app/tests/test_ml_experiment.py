import ml_experiment as ml


def create_campaign(client, name):
    return client.post("/campaigns", json={"name": name}).json()["id"]


def import_and_qualify(client, campaign_id, industry="IT", label=None, notes=None):
    csv_bytes = (
        f"name,company,industry,country,email,website\n"
        f"Lead {campaign_id}-{industry},Co{campaign_id}{industry},{industry},Macedonia,l{campaign_id}{industry}@co.example,\n"
    ).encode()
    client.post(f"/campaigns/{campaign_id}/leads/import", files={"file": ("leads.csv", csv_bytes, "text/csv")})
    lead = client.get(f"/campaigns/{campaign_id}/leads").json()[-1]
    client.post(f"/leads/{lead['id']}/qualify")
    if label:
        client.post(f"/leads/{lead['id']}/qualification/labels", json={"label": label, "notes": notes})
    return lead


def get_db_session():
    import database

    return database.SessionLocal()


# --- readiness assessment on the (empty, freshly reset) test database ---------

def test_label_readiness_api_endpoint(client):
    resp = client.get("/ml/label-readiness")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_labeled_leads"] == 0
    assert body["sufficient_for_training"] is False
    assert isinstance(body["reasons"], list) and len(body["reasons"]) > 0


def test_assess_readiness_reports_zero_on_fresh_database(client):
    db = get_db_session()
    try:
        assessment = ml.assess_label_readiness(db)
    finally:
        db.close()

    assert assessment.total_labeled_leads == 0
    assert assessment.unique_companies == 0
    assert assessment.unique_campaigns == 0
    assert assessment.sufficient_for_training is False
    assert len(assessment.reasons) > 0


def test_training_refuses_and_explains_when_data_is_insufficient(client):
    campaign_id = create_campaign(client, "Small Campaign")
    client.post(f"/campaigns/{campaign_id}/criteria", json={"target_industries": ["IT"]})
    import_and_qualify(client, campaign_id, industry="IT", label="suitable")
    import_and_qualify(client, campaign_id, industry="Finance", label="unsuitable")

    db = get_db_session()
    try:
        with_raises = None
        try:
            ml.train_suitability_model(db)
        except ml.InsufficientLabelDataError as e:
            with_raises = e
        assert with_raises is not None
        assert with_raises.assessment.total_labeled_leads == 2
        assert len(with_raises.assessment.reasons) > 0
    finally:
        db.close()


def _svc_db():
    import database

    return database.SessionLocal()


# --- feature extraction is deterministic and matches the qualification fields --

def test_extract_features_matches_qualification_fields(client):
    campaign_id = create_campaign(client, "Feature Co")
    client.post(f"/campaigns/{campaign_id}/criteria", json={"target_industries": ["IT"]})
    lead = import_and_qualify(client, campaign_id)

    db = get_db_session()
    try:
        db_lead = db.get(__import__("database").Lead, lead["id"])
        features = ml.extract_features(db_lead.qualification)
        assert features == [100.0, 1.0, 1, 0, 0, 0]
        assert len(features) == len(ml.FEATURE_NAMES)
    finally:
        db.close()


# --- training table excludes leads whose qualification drifted since labeling --

def test_build_training_table_excludes_stale_label_qualification_pairs(client):
    campaign_id = create_campaign(client, "Drift Co")
    client.post(f"/campaigns/{campaign_id}/criteria", json={"target_industries": ["IT"]})
    lead = import_and_qualify(client, campaign_id, label="suitable")

    # Criteria change AFTER labeling -> qualification is now stale relative to
    # what was reviewed; re-qualifying moves criteria_version out of sync with
    # the label's criteria_version_reviewed until it's re-labeled.
    client.post(f"/campaigns/{campaign_id}/criteria", json={"target_industries": ["Finance"]})
    client.post(f"/leads/{lead['id']}/qualify")  # now criteria_version=2, but label recorded version=1

    db = get_db_session()
    try:
        X, y, groups = ml.build_training_table(db)
        assert X == [] and y == [] and groups == []
    finally:
        db.close()


def test_build_training_table_includes_matching_pairs(client):
    campaign_id = create_campaign(client, "Match Co")
    client.post(f"/campaigns/{campaign_id}/criteria", json={"target_industries": ["IT"]})
    lead = import_and_qualify(client, campaign_id, label="suitable")

    db = get_db_session()
    try:
        import database

        db_lead = db.get(database.Lead, lead["id"])
        expected_group = ml._company_group_key(db_lead)

        X, y, groups = ml.build_training_table(db)
        assert len(X) == 1
        assert y == ["suitable"]
        assert groups == [expected_group]
        assert groups != [campaign_id]  # grouping is by company identity, not campaign
    finally:
        db.close()


# --- regression: same company across different campaigns must not leak -------

def test_same_company_in_different_campaigns_gets_the_same_group_key(client):
    """Regression for the campaign-grouping bug: a company re-imported into a
    second campaign must be treated as the SAME group, not split across two
    groups just because it landed in a different campaign -- otherwise
    GroupKFold could put one of its leads in training and the other in the
    held-out fold, leaking company-specific signal across the split."""
    campaign_a = create_campaign(client, "Campaign A")
    campaign_b = create_campaign(client, "Campaign B")

    csv_a = (
        "name,company,industry,country,email,website\n"
        "Contact A,Acme Corp,IT,Macedonia,contactA@acme.com,https://acme.com/\n"
    ).encode()
    csv_b = (
        "name,company,industry,country,email,website\n"
        "Contact B,Acme Corp,IT,Macedonia,contactB@acme.com,acme.com\n"  # same domain, different formatting
    ).encode()
    client.post(f"/campaigns/{campaign_a}/leads/import", files={"file": ("leads.csv", csv_a, "text/csv")})
    client.post(f"/campaigns/{campaign_b}/leads/import", files={"file": ("leads.csv", csv_b, "text/csv")})

    lead_a = client.get(f"/campaigns/{campaign_a}/leads").json()[0]
    lead_b = client.get(f"/campaigns/{campaign_b}/leads").json()[0]
    assert lead_a["campaign_id"] != lead_b["campaign_id"]  # genuinely two different campaigns

    client.post(f"/campaigns/{campaign_a}/criteria", json={"target_industries": ["IT"]})
    client.post(f"/campaigns/{campaign_b}/criteria", json={"target_industries": ["IT"]})
    client.post(f"/leads/{lead_a['id']}/qualify")
    client.post(f"/leads/{lead_b['id']}/qualify")
    client.post(f"/leads/{lead_a['id']}/qualification/labels", json={"label": "suitable"})
    client.post(f"/leads/{lead_b['id']}/qualification/labels", json={"label": "unsuitable"})

    db = get_db_session()
    try:
        import database

        db_lead_a = db.get(database.Lead, lead_a["id"])
        db_lead_b = db.get(database.Lead, lead_b["id"])
        assert ml._company_group_key(db_lead_a) == ml._company_group_key(db_lead_b) == "domain:acme.com"

        X, y, groups = ml.build_training_table(db)
        assert len(set(groups)) == 1  # both leads collapse into a single group -- never split across a CV fold

        assessment = ml.assess_label_readiness(db)
        assert assessment.unique_companies == 1  # NOT 2 -- this is the bug this test guards against
        assert assessment.unique_campaigns == 2  # campaign diversity is still reported separately
    finally:
        db.close()


def test_company_fallback_grouping_uses_normalized_name_when_no_website(client):
    campaign_a = create_campaign(client, "Campaign A2")
    campaign_b = create_campaign(client, "Campaign B2")
    csv_a = "name,company,industry,country,email,website\nX,Beta LLC,IT,Macedonia,x@beta.example,\n".encode()
    csv_b = "name,company,industry,country,email,website\nY,  beta llc  ,IT,Macedonia,y@beta.example,\n".encode()
    client.post(f"/campaigns/{campaign_a}/leads/import", files={"file": ("leads.csv", csv_a, "text/csv")})
    client.post(f"/campaigns/{campaign_b}/leads/import", files={"file": ("leads.csv", csv_b, "text/csv")})

    lead_a = client.get(f"/campaigns/{campaign_a}/leads").json()[0]
    lead_b = client.get(f"/campaigns/{campaign_b}/leads").json()[0]

    db = get_db_session()
    try:
        import database

        db_lead_a = db.get(database.Lead, lead_a["id"])
        db_lead_b = db.get(database.Lead, lead_b["id"])
        assert ml._company_group_key(db_lead_a) == ml._company_group_key(db_lead_b) == "name:beta llc"
    finally:
        db.close()


# --- sample counts alone must not be enough to prove readiness ----------------

def test_high_sample_count_is_still_insufficient_when_companies_are_too_concentrated(client):
    """Constructs a dataset that clears every raw sample-count threshold
    (>= MIN_TOTAL_SAMPLES, >= MIN_SAMPLES_PER_CLASS for every class) purely by
    relabeling/re-importing the SAME two companies across many campaigns --
    which proves nothing about generalization. Readiness must still say no."""
    dominant_domain = "dominant.example"
    other_domain = "other.example"

    for i in range(20):  # 20 samples, all "Dominant Co" (same domain), 10 suitable / 10 unsuitable
        campaign_id = create_campaign(client, f"Dominant Campaign {i}")
        client.post(f"/campaigns/{campaign_id}/criteria", json={"target_industries": ["IT"]})
        csv_bytes = (
            f"name,company,industry,country,email,website\n"
            f"Contact {i},Dominant Co,IT,Macedonia,c{i}@dominant.example,{dominant_domain}\n"
        ).encode()
        client.post(f"/campaigns/{campaign_id}/leads/import", files={"file": ("leads.csv", csv_bytes, "text/csv")})
        lead = client.get(f"/campaigns/{campaign_id}/leads").json()[0]
        client.post(f"/leads/{lead['id']}/qualify")
        label = "suitable" if i % 2 == 0 else "unsuitable"
        client.post(f"/leads/{lead['id']}/qualification/labels", json={"label": label})

    for i in range(10):  # 10 more samples, all "Other Co" (same domain), all "unsure"
        campaign_id = create_campaign(client, f"Other Campaign {i}")
        client.post(f"/campaigns/{campaign_id}/criteria", json={"target_industries": ["IT"]})
        csv_bytes = (
            f"name,company,industry,country,email,website\n"
            f"Contact O{i},Other Co,IT,Macedonia,o{i}@other.example,{other_domain}\n"
        ).encode()
        client.post(f"/campaigns/{campaign_id}/leads/import", files={"file": ("leads.csv", csv_bytes, "text/csv")})
        lead = client.get(f"/campaigns/{campaign_id}/leads").json()[0]
        client.post(f"/leads/{lead['id']}/qualify")
        client.post(f"/leads/{lead['id']}/qualification/labels", json={"label": "unsure"})

    db = get_db_session()
    try:
        assessment = ml.assess_label_readiness(db)

        # Raw counts alone would look "sufficient": 30 total, 10/10/10 per class.
        assert assessment.total_labeled_leads == 30
        assert all(c >= ml.MIN_SAMPLES_PER_CLASS for c in assessment.class_counts.values())

        # But it must still be rejected -- only 2 distinct companies overall,
        # one company dominates far past the concentration limit, and the
        # "suitable"/"unsuitable" classes are each backed by exactly 1 company.
        assert assessment.unique_companies == 2
        assert assessment.sufficient_for_training is False
        assert len(assessment.reasons) > 0
        assert any("distinct compan" in r for r in assessment.reasons)
    finally:
        db.close()


# --- a genuinely sufficient synthetic dataset actually trains ------------------

def test_training_succeeds_with_a_synthetic_sufficient_dataset(client):
    """Builds enough synthetic labeled leads across enough campaigns to clear
    every readiness threshold, and confirms an actual LogisticRegression gets
    fit and cross-validated -- proving the scaffolding is real, runnable code,
    not just a stub."""
    campaigns = [create_campaign(client, f"Synthetic {i}") for i in range(4)]
    for c in campaigns:
        client.post(f"/campaigns/{c}/criteria", json={"target_industries": ["IT"]})

    per_campaign = (ml.MIN_TOTAL_SAMPLES // len(campaigns)) + 2
    for c in campaigns:
        for i in range(per_campaign):
            industry = "IT" if i % 2 == 0 else "Finance"
            label = "suitable" if i % 2 == 0 else "unsuitable"
            csv_bytes = (
                f"name,company,industry,country,email,website\n"
                f"P{c}-{i},Co{c}-{i},{industry},Macedonia,p{c}{i}@co.example,\n"
            ).encode()
            client.post(f"/campaigns/{c}/leads/import", files={"file": ("leads.csv", csv_bytes, "text/csv")})
            lead = client.get(f"/campaigns/{c}/leads").json()[-1]
            client.post(f"/leads/{lead['id']}/qualify")
            client.post(f"/leads/{lead['id']}/qualification/labels", json={"label": label})
        # Give every campaign a few distinct "unsure"-labeled leads too, so all
        # 3 classes clear the per-class minimum (a *lead* is one sample --
        # relabeling the same lead repeatedly would not add samples).
        for j in range(3):
            csv_bytes = f"name,company,industry,country,email,website\nU{c}-{j},CoU{c}-{j},IT,Macedonia,u{c}{j}@co.example,\n".encode()
            client.post(f"/campaigns/{c}/leads/import", files={"file": ("leads.csv", csv_bytes, "text/csv")})
            lead = client.get(f"/campaigns/{c}/leads").json()[-1]
            client.post(f"/leads/{lead['id']}/qualify")
            client.post(f"/leads/{lead['id']}/qualification/labels", json={"label": "unsure"})

    db = get_db_session()
    try:
        assessment = ml.assess_label_readiness(db)
        assert assessment.sufficient_for_training, assessment.reasons

        report = ml.train_suitability_model(db)
        assert report["n_samples"] == assessment.total_labeled_leads
        assert len(report["cv_balanced_accuracy_per_fold"]) == report["n_splits"]
        assert hasattr(report["model"], "coef_")  # actually fit
    finally:
        db.close()
