from evaluation import metrics
from evaluation.dataset import EvalCase, EvalFact


def _case(**overrides):
    defaults = dict(
        id="t1", company="Acme", contact_name="Jane", industry="IT", country="Macedonia",
        website="acme.example", source_excerpt="", tags=["normal"], notes="", expected_facts=[],
    )
    defaults.update(overrides)
    return EvalCase(**defaults)


def test_check_language_detects_macedonian_cyrillic_text():
    result = metrics.check_language("Здраво, ова е порака на македонски јазик.", "mk")
    assert result.compliant is True
    assert result.cyrillic_ratio > 0.5


def test_check_language_flags_wrong_alphabet_for_macedonian():
    result = metrics.check_language("Hello, this is an English message.", "mk")
    assert result.compliant is False


def test_check_language_accepts_english_latin_text():
    result = metrics.check_language("Hello, this is an English message.", "en")
    assert result.compliant is True


def test_check_length_short_bucket():
    result = metrics.check_length("One sentence. Two sentence.", "short")
    assert result.sentence_count == 2
    assert result.compliant is True


def test_check_length_too_long_for_short_bucket():
    long_body = " ".join(f"Sentence number {i}." for i in range(20))
    result = metrics.check_length(long_body, "short")
    assert result.compliant is False


def test_check_source_refs_not_applicable_for_basic_approach():
    case = _case(expected_facts=[EvalFact(category="offering", text="Sells widgets")])
    result = metrics.check_source_refs(claims=[], case=case, applicable=False)
    assert result.applicable is False
    assert result.invalid_count == 0


def test_check_source_refs_flags_a_fact_id_that_does_not_resolve():
    case = _case(expected_facts=[EvalFact(category="offering", text="Sells widgets")])

    class FakeClaim:
        fact_id = 999
        claim = "They sell rockets"

    result = metrics.check_source_refs(claims=[FakeClaim()], case=case, applicable=True)
    assert result.applicable is True
    assert result.invalid_count == 1
    assert result.verified_count == 0


def test_check_source_refs_verifies_a_claim_with_a_valid_fact_id():
    case = _case(expected_facts=[EvalFact(category="offering", text="Sells widgets")])

    class FakeClaim:
        fact_id = 0
        claim = "They sell widgets"

    result = metrics.check_source_refs(claims=[FakeClaim()], case=case, applicable=True)
    assert result.verified_count == 1
    assert result.invalid_count == 0


def test_check_unsupported_claims_flags_invented_funding_language():
    case = _case(expected_facts=[EvalFact(category="offering", text="Sells widgets")])
    result = metrics.check_unsupported_claims("We heard you just raised a funding round.", case)
    assert result.count >= 1
    assert result.flags[0]["category"] == "funding_or_investment"


def test_check_unsupported_claims_allows_language_that_matches_a_verified_fact():
    case = _case(expected_facts=[EvalFact(category="fact", text="Recently hired a new CFO after a funding round")])
    result = metrics.check_unsupported_claims("They mentioned a funding round on their site.", case)
    assert result.count == 0


def test_compute_metrics_returns_all_sections():
    case = _case(expected_facts=[EvalFact(category="offering", text="Sells widgets")])
    result = metrics.compute_metrics(
        case=case, approach="evidence_based", subject="Hi", body="Hello there, we sell things.",
        claims=[], language="en", length="short", schema_valid=True, schema_note="ok",
    )
    assert result.schema_valid is True
    assert result.source_refs.applicable is True
    assert result.language.expected == "en"
    assert result.length.expected == "short"
    assert result.unsupported_claims.method == "draft_safety_regex_heuristic_v1"
