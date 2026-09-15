import pytest

from evaluation.dataset import load_dataset
from evaluation import harness


def test_mock_mode_runs_both_approaches_for_every_case_with_no_errors():
    dataset = load_dataset()
    outcomes = harness.run(dataset, mode="mock")
    assert len(outcomes) == len(dataset.cases) * 2
    assert all(o.error is None for _, o in outcomes)


def test_mock_mode_never_reports_latency_or_tokens():
    """Mocked runs verify the harness only -- fabricated latency/token
    numbers from a fake call would be worse than no numbers at all."""
    dataset = load_dataset()
    outcomes = harness.run(dataset, mode="mock", case_ids=["eval-001"])
    for _, outcome in outcomes:
        assert outcome.latency_ms is None
        assert outcome.prompt_tokens is None
        assert outcome.total_tokens is None


def test_mock_mode_is_deterministic():
    dataset = load_dataset()
    run1 = harness.run(dataset, mode="mock", case_ids=["eval-001", "eval-020"])
    run2 = harness.run(dataset, mode="mock", case_ids=["eval-001", "eval-020"])
    bodies1 = [o.body for _, o in run1]
    bodies2 = [o.body for _, o in run2]
    assert bodies1 == bodies2


def test_live_mode_requires_an_explicit_max_calls_budget():
    dataset = load_dataset()
    with pytest.raises(ValueError, match="max_calls"):
        harness.run(dataset, mode="live", case_ids=["eval-001"])


def test_live_mode_never_exceeds_the_call_budget():
    """Uses a fake call_model indirectly by running in live mode with a tiny
    budget against real generator code -- since no real network call can
    reach OpenAI without a key, this only asserts the budget-stopping logic
    itself, not real API behavior (that requires an actual --mode live run,
    see evaluation/run_eval.py)."""
    dataset = load_dataset()
    outcomes = harness.run(dataset, mode="live", max_calls=1, case_ids=["eval-001", "eval-002", "eval-003"])
    assert len(outcomes) == 1


def test_unsupported_claim_bait_case_is_flagged_by_the_mock_evidence_based_output():
    dataset = load_dataset()
    outcomes = harness.run(dataset, mode="mock", case_ids=["eval-020"])
    evidence_outcome = next(o for c, o in outcomes if o.approach == "evidence_based")
    assert evidence_outcome.metrics.unsupported_claims.count >= 1


def test_missing_info_case_still_produces_a_generic_non_crashing_output():
    dataset = load_dataset()
    outcomes = harness.run(dataset, mode="mock", case_ids=["eval-011"])
    assert all(o.error is None for _, o in outcomes)
    assert all(o.body for _, o in outcomes)
