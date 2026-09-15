"""Unit tests for the pure, deterministic scoring function -- no DB, no HTTP."""
from datetime import datetime, timezone

import qualification_service as qs
from database import Lead, CampaignCriteria, CompanyResearch, ResearchFact


def make_criteria(**overrides):
    defaults = dict(
        id=1,
        campaign_id=1,
        version=1,
        product_service="B2B analytics platform",
        target_industries=["IT", "Finance"],
        target_countries=["Macedonia"],
        preferred_company_size="",
        business_needs=[],
        exclusion_criteria=[],
        weights=dict(qs.DEFAULT_WEIGHTS),
    )
    defaults.update(overrides)
    return CampaignCriteria(**defaults)


def make_lead(**overrides):
    defaults = dict(id=1, campaign_id=1, name="Lead", company="Acme", industry="IT", country="Macedonia", dedup_key="k")
    defaults.update(overrides)
    return Lead(**defaults)


def make_research_fact(**overrides):
    defaults = dict(
        id=1,
        research_id=1,
        category="offering",
        key=None,
        text="",
        excerpt="x",
        source_url="https://acme.example/",
        retrieved_at=datetime.now(timezone.utc),
        conflicting=False,
    )
    defaults.update(overrides)
    return ResearchFact(**defaults)


def attach_research(lead, *, status="completed", facts=None):
    research = CompanyResearch(id=1, lead_id=lead.id, website="acme.example", status=status, version=1)
    research.facts = facts or []
    lead.research = research
    return research


# --- industry / country: matched, unmatched, unknown --------------------------

def test_industry_and_country_matched_from_lead_fields():
    lead = make_lead(industry="IT", country="Macedonia")
    criteria = make_criteria()
    result = qs.evaluate_lead(lead, criteria)

    assert {m["criterion"] for m in result["matched"]} == {"industry", "country"}
    assert result["unmatched"] == []
    assert result["unknown"] == []
    assert result["fit_score"] == 100.0
    assert result["evidence_coverage"] == 1.0


def test_industry_unmatched_when_not_in_target_list():
    lead = make_lead(industry="Retail", country="Macedonia")
    criteria = make_criteria()
    result = qs.evaluate_lead(lead, criteria)

    industry_entries = [e for e in result["unmatched"] if e["criterion"] == "industry"]
    assert len(industry_entries) == 1
    assert result["fit_score"] == 50.0  # 1 of 2 equally-weighted known criteria matched


def test_industry_unknown_when_lead_field_blank():
    lead = make_lead(industry=None, country="Macedonia")
    criteria = make_criteria()
    result = qs.evaluate_lead(lead, criteria)

    assert any(e["criterion"] == "industry" for e in result["unknown"])
    assert result["evidence_coverage"] == 0.5  # only country was knowable
    assert result["fit_score"] == 100.0  # country matched; industry excluded from scoring, not penalized


def test_unconfigured_criteria_are_not_counted_at_all():
    lead = make_lead(industry="IT", country="Macedonia")
    criteria = make_criteria(target_industries=[], target_countries=[])
    result = qs.evaluate_lead(lead, criteria)
    assert result["matched"] == result["unmatched"] == result["unknown"] == []
    assert result["fit_score"] is None
    assert result["evidence_coverage"] == 0.0


# --- company size: needs completed research -----------------------------------

def test_company_size_unknown_without_completed_research():
    lead = make_lead(industry="IT", country="Macedonia")
    criteria = make_criteria(preferred_company_size="50-200")
    result = qs.evaluate_lead(lead, criteria)
    assert any(e["criterion"] == "company_size" for e in result["unknown"])


def test_company_size_matched_from_validated_research_fact():
    lead = make_lead(industry="IT", country="Macedonia")
    attach_research(lead, facts=[make_research_fact(category="fact", key="company_size", text="We have 120 employees")])
    criteria = make_criteria(preferred_company_size="120")
    result = qs.evaluate_lead(lead, criteria)

    size_matches = [e for e in result["matched"] if e["criterion"] == "company_size"]
    assert len(size_matches) == 1
    assert size_matches[0]["source"]["type"] == "research_fact"


def test_company_size_unmatched_when_fact_present_but_disagrees():
    lead = make_lead(industry="IT", country="Macedonia")
    attach_research(lead, facts=[make_research_fact(category="fact", key="company_size", text="We have 5 employees")])
    criteria = make_criteria(preferred_company_size="500")
    result = qs.evaluate_lead(lead, criteria)
    assert any(e["criterion"] == "company_size" for e in result["unmatched"])


def test_company_size_unknown_when_evidence_conflicts():
    lead = make_lead(industry="IT", country="Macedonia")
    attach_research(
        lead,
        facts=[
            make_research_fact(category="fact", key="company_size", text="5 employees", conflicting=True),
            make_research_fact(category="fact", key="company_size", text="500 employees", conflicting=True),
        ],
    )
    criteria = make_criteria(preferred_company_size="5")
    result = qs.evaluate_lead(lead, criteria)
    assert any(e["criterion"] == "company_size" for e in result["unknown"])
    assert not any(e["criterion"] == "company_size" for e in result["matched"])


# --- business needs: one criterion instance per phrase -------------------------

def test_business_need_matched_via_offering_fact():
    lead = make_lead(industry="IT", country="Macedonia")
    attach_research(lead, facts=[make_research_fact(category="offering", text="We provide inventory forecasting tools")])
    criteria = make_criteria(business_needs=["inventory forecasting"])
    result = qs.evaluate_lead(lead, criteria)
    assert any(e["criterion"] == "business_need:inventory forecasting" for e in result["matched"])


def test_business_need_unmatched_when_research_done_but_no_match():
    lead = make_lead(industry="IT", country="Macedonia")
    attach_research(lead, facts=[make_research_fact(category="offering", text="We sell office furniture")])
    criteria = make_criteria(business_needs=["inventory forecasting"])
    result = qs.evaluate_lead(lead, criteria)
    assert any(e["criterion"] == "business_need:inventory forecasting" for e in result["unmatched"])


def test_multiple_business_needs_are_independent_criterion_instances():
    lead = make_lead(industry="IT", country="Macedonia")
    attach_research(lead, facts=[make_research_fact(category="offering", text="We provide inventory forecasting tools")])
    criteria = make_criteria(business_needs=["inventory forecasting", "payroll automation"])
    result = qs.evaluate_lead(lead, criteria)

    matched_criteria = {e["criterion"] for e in result["matched"]}
    unmatched_criteria = {e["criterion"] for e in result["unmatched"]}
    assert "business_need:inventory forecasting" in matched_criteria
    assert "business_need:payroll automation" in unmatched_criteria


# --- exclusions: separate from matched/unmatched/unknown, never auto-zero ------

def test_exclusion_triggered_from_lead_field_does_not_zero_the_score():
    lead = make_lead(industry="IT", country="Macedonia")
    criteria = make_criteria(exclusion_criteria=["Macedonia"])
    result = qs.evaluate_lead(lead, criteria)

    assert len(result["exclusions"]) == 1
    assert result["exclusions"][0]["criterion"] == "exclusion:Macedonia"
    assert result["fit_score"] == 100.0  # exclusions never silently alter the score


def test_exclusion_triggered_from_research_fact():
    lead = make_lead(industry="IT", country="Macedonia")
    attach_research(lead, facts=[make_research_fact(category="fact", key="legal_status", text="Currently in bankruptcy proceedings")])
    criteria = make_criteria(exclusion_criteria=["bankruptcy"])
    result = qs.evaluate_lead(lead, criteria)
    assert len(result["exclusions"]) == 1
    assert result["exclusions"][0]["source"]["type"] == "research_fact"


def test_no_exclusion_when_phrase_does_not_appear_anywhere():
    lead = make_lead(industry="IT", country="Macedonia")
    criteria = make_criteria(exclusion_criteria=["competitor"])
    result = qs.evaluate_lead(lead, criteria)
    assert result["exclusions"] == []


# --- weights actually change the score ------------------------------------------

def test_custom_weights_change_fit_score():
    lead = make_lead(industry="Retail", country="Macedonia")  # industry unmatched, country matched
    criteria = make_criteria(weights={"industry": 3.0, "country": 1.0})
    result = qs.evaluate_lead(lead, criteria)
    # matched weight (country=1) / known weight (industry=3 + country=1) = 25%
    assert result["fit_score"] == 25.0


# --- evidence_coverage vs fit_score are reported separately, never combined ----

def test_evidence_coverage_and_fit_score_are_distinct_fields():
    lead = make_lead(industry="IT", country=None)
    criteria = make_criteria(preferred_company_size="50", business_needs=["automation"])
    result = qs.evaluate_lead(lead, criteria)
    assert "fit_score" in result and "evidence_coverage" in result
    assert result["fit_score"] != result["evidence_coverage"]
    # 4 configured criteria instances (industry, country, company_size, business_need); only industry is known
    assert result["evidence_coverage"] == 0.25
