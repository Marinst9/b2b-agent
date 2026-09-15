from modules import draft_safety
from modules.draft_generator import DraftClaim


class FakeFact:
    def __init__(self, id, text):
        self.id = id
        self.text = text


def test_verify_claims_accepts_claim_referencing_a_valid_fact():
    facts = [FakeFact(1, "Sells widgets to retailers")]
    claims = [DraftClaim(claim="They sell widgets", fact_id=1)]
    verified, flags = draft_safety.verify_claims(claims, facts)
    assert len(verified) == 1
    assert flags == []


def test_verify_claims_rejects_claim_with_invalid_fact_id():
    facts = [FakeFact(1, "Sells widgets to retailers")]
    claims = [DraftClaim(claim="They raised $10M", fact_id=999)]
    verified, flags = draft_safety.verify_claims(claims, facts)
    assert verified == []
    assert len(flags) == 1
    assert flags[0]["type"] == "invalid_fact_reference"


def test_verify_claims_rejects_fact_id_from_a_different_research_snapshot():
    """A fact_id that belongs to some OTHER lead's research (or a prior,
    superseded snapshot of this lead's own research) must not be trusted just
    because the number happens to exist somewhere in the database -- only
    ids present in the exact facts passed in for THIS generation are valid."""
    facts_shown_to_model = [FakeFact(5, "Sells widgets")]
    claims = [DraftClaim(claim="They sell gadgets", fact_id=7)]  # id 7 belongs to a different snapshot
    verified, flags = draft_safety.verify_claims(claims, facts_shown_to_model)
    assert verified == []
    assert flags[0]["type"] == "invalid_fact_reference"


# --- unsupported inference scanning: matching fact_id alone is not enough -----

def test_scan_flags_funding_language_not_grounded_in_any_fact():
    facts = [FakeFact(1, "Sells widgets to retailers")]
    body = "We noticed your company recently raised $5M in a funding round."
    flags = draft_safety.scan_for_unsupported_inferences(body, facts)
    assert any(f["category"] == "funding_or_investment" for f in flags)


def test_scan_flags_growth_and_staffing_language():
    facts = [FakeFact(1, "Sells widgets")]
    body = "Congrats on your growing team -- you recently hired a lot of new staff!"
    flags = draft_safety.scan_for_unsupported_inferences(body, facts)
    assert any(f["category"] == "growth_or_staffing" for f in flags)


def test_scan_flags_business_problem_language():
    facts = [FakeFact(1, "Sells widgets")]
    body = "We know you're struggling with inventory forecasting."
    flags = draft_safety.scan_for_unsupported_inferences(body, facts)
    assert any(f["category"] == "business_problem" for f in flags)


def test_scan_flags_previous_contact_language():
    facts = [FakeFact(1, "Sells widgets")]
    body = "Following up on our call from last week, here's more info."
    flags = draft_safety.scan_for_unsupported_inferences(body, facts)
    assert any(f["category"] == "previous_contact" for f in flags)


def test_scan_does_not_flag_language_actually_grounded_in_a_fact():
    """The same danger phrase, when it literally appears in a verified fact's
    own text, must not be flagged as unsupported."""
    facts = [FakeFact(1, "The company raised $5M in a funding round in 2023, per their press page.")]
    body = "We saw that your company raised $5M in a funding round -- congratulations!"
    flags = draft_safety.scan_for_unsupported_inferences(body, facts)
    assert flags == []


def test_scan_returns_no_flags_for_a_clean_generic_body():
    facts = [FakeFact(1, "Sells widgets")]
    body = "We think our platform could help your team save time on outreach."
    flags = draft_safety.scan_for_unsupported_inferences(body, facts)
    assert flags == []
