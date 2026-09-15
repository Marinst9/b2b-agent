from evaluation.dataset import load_dataset

REQUIRED_TAGS = {"normal", "missing_info", "conflicting_info", "irrelevant_info", "unsupported_claim_bait", "prompt_injection"}


def test_dataset_loads_and_has_20_to_30_cases():
    dataset = load_dataset()
    assert 20 <= len(dataset.cases) <= 30


def test_every_case_is_marked_synthetic_and_has_an_id_and_company():
    dataset = load_dataset()
    ids = set()
    for case in dataset.cases:
        assert case.id not in ids, f"duplicate case id {case.id}"
        ids.add(case.id)
        assert case.company
        assert case.tags, f"case {case.id} has no tags"


def test_every_required_category_is_represented():
    dataset = load_dataset()
    all_tags = {tag for case in dataset.cases for tag in case.tags}
    missing = REQUIRED_TAGS - all_tags
    assert not missing, f"dataset is missing required categories: {missing}"


def test_by_tag_filters_correctly():
    dataset = load_dataset()
    injection_cases = dataset.by_tag("prompt_injection")
    assert len(injection_cases) >= 2
    assert all("prompt_injection" in c.tags for c in injection_cases)


def test_conflicting_info_cases_have_facts_marked_conflicting():
    dataset = load_dataset()
    for case in dataset.by_tag("conflicting_info"):
        assert any(f.conflicting for f in case.expected_facts), f"case {case.id} tagged conflicting_info but no fact marked conflicting=true"


def test_missing_info_cases_have_little_or_no_evidence():
    dataset = load_dataset()
    for case in dataset.by_tag("missing_info"):
        assert len(case.expected_facts) <= 1


def test_dataset_has_a_campaign_offering_and_version():
    dataset = load_dataset()
    assert dataset.version
    assert dataset.campaign_offering
