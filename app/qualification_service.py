"""Deterministic, explainable lead qualification against campaign criteria.

SCORING FORMULA (rubric_version = "v1")
----------------------------------------
Campaign criteria configure a small set of weighted CRITERION INSTANCES:
  - "industry"                 (1 instance, only if target_industries is set)
  - "country"                  (1 instance, only if target_countries is set)
  - "company_size"             (1 instance, only if preferred_company_size is set)
  - "business_need:<phrase>"   (1 instance per configured business-need phrase)

Each instance is checked against available EVIDENCE:
  - "industry" / "country" are checked against the lead's own imported
    Lead.industry / Lead.country fields -- first-party structured import
    data, not scraped, cited as a {"type": "lead_field", ...} source.
  - "company_size" and "business_need:<phrase>" are checked against the
    lead's *validated* ResearchFact rows -- already excerpt-verified and
    conflict-flagged upstream by the research module -- cited as a
    {"type": "research_fact", ...} source with id/source_url/excerpt.

Each instance resolves to exactly one of:
  - MATCHED    evidence found and it satisfies the criterion.
  - UNMATCHED  evidence found (a lead field is set / research completed) but
               it does NOT satisfy the criterion.
  - UNKNOWN    no evidence available to judge this criterion at all (field
               blank, research not completed, or the only evidence conflicts
               across sources with no clear resolution). UNKNOWN is a first
               -class outcome, never silently folded into MATCHED/UNMATCHED.

fit_score (0-100), and evidence_coverage (0-1), are DELIBERATELY SEPARATE:

    K = criterion instances that are MATCHED or UNMATCHED (i.e. "known").
    UNKNOWN instances are excluded entirely from fit_score -- they neither
    help nor hurt it.

    fit_score = null                                   if K is empty
              = 100 * sum(weight_i for i in K if MATCHED) / sum(weight_i for i in K)
                                                          otherwise

    evidence_coverage = |K| / (total configured criterion instances)

A high fit_score computed from very low evidence_coverage is much weaker
evidence than the same score with full coverage -- that is exactly why the
two numbers are reported separately rather than combined into one.

Exclusions are evaluated independently of the weighted score: each configured
exclusion phrase is checked against the same evidence (lead fields + validated
research facts) and, if matched, appears in `exclusions`. fit_score is NOT
auto-zeroed by a triggered exclusion -- the full evidence trail stays visible
and the human reviewer decides how to weigh it.

This is a plain rule-based match rate over user-configured weights. It is NOT
a probability of conversion, a sales forecast, or a learned model -- see
ml_experiment.py for the separate, optional statistical candidate that
predicts human suitability LABELS (not conversion).
"""
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from database import Lead, Campaign, CampaignCriteria, Qualification, QualificationLabel

RUBRIC_VERSION = "v1"

DEFAULT_WEIGHTS = {"industry": 1.0, "country": 1.0, "company_size": 1.0, "business_needs": 1.0}

# Research fact `key` slugs treated as company-size evidence. The extractor's
# `key` field is free-form, so this is a best-effort allowlist, not a hard schema.
_SIZE_FACT_KEYS = {"company_size", "employee_count", "team_size", "headcount", "company_size_range", "size"}

VALID_LABELS = {"suitable", "unsuitable", "unsure"}


class QualificationServiceError(Exception):
    """Raised for request-level errors that should map to a 4xx HTTP response."""


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


@dataclass
class CriterionResult:
    criterion: str
    detail: str
    weight: float
    source: Optional[dict]


def _lead_field_source(field_name: str, value: str) -> dict:
    return {"type": "lead_field", "field": field_name, "value": value}


def _fact_source(fact) -> dict:
    return {
        "type": "research_fact",
        "fact_id": fact.id,
        "source_url": fact.source_url,
        "excerpt": fact.excerpt,
        "retrieved_at": fact.retrieved_at.isoformat() if fact.retrieved_at else None,
        "conflicting": fact.conflicting,
    }


def _research_ready(lead: Lead) -> bool:
    return lead.research is not None and lead.research.status == "completed"


# --- per-criterion evaluators --------------------------------------------------

def _evaluate_industry(lead: Lead, criteria: CampaignCriteria):
    if not criteria.target_industries:
        return None
    weight = criteria.weights.get("industry", DEFAULT_WEIGHTS["industry"])
    if not lead.industry:
        return "unknown", CriterionResult("industry", "Lead has no industry recorded.", weight, None)
    targets = {_norm(t) for t in criteria.target_industries}
    if _norm(lead.industry) in targets:
        return "matched", CriterionResult(
            "industry", f"Lead industry '{lead.industry}' is a target industry.", weight, _lead_field_source("industry", lead.industry)
        )
    return "unmatched", CriterionResult(
        "industry",
        f"Lead industry '{lead.industry}' is not in target industries {sorted(criteria.target_industries)}.",
        weight,
        _lead_field_source("industry", lead.industry),
    )


def _evaluate_country(lead: Lead, criteria: CampaignCriteria):
    if not criteria.target_countries:
        return None
    weight = criteria.weights.get("country", DEFAULT_WEIGHTS["country"])
    if not lead.country:
        return "unknown", CriterionResult("country", "Lead has no country recorded.", weight, None)
    targets = {_norm(t) for t in criteria.target_countries}
    if _norm(lead.country) in targets:
        return "matched", CriterionResult(
            "country", f"Lead country '{lead.country}' is a target country.", weight, _lead_field_source("country", lead.country)
        )
    return "unmatched", CriterionResult(
        "country",
        f"Lead country '{lead.country}' is not in target countries {sorted(criteria.target_countries)}.",
        weight,
        _lead_field_source("country", lead.country),
    )


def _evaluate_company_size(lead: Lead, criteria: CampaignCriteria):
    if not criteria.preferred_company_size:
        return None
    weight = criteria.weights.get("company_size", DEFAULT_WEIGHTS["company_size"])
    if not _research_ready(lead):
        return "unknown", CriterionResult("company_size", "No completed research available to check company size.", weight, None)

    size_facts = [f for f in lead.research.facts if f.category == "fact" and (f.key or "") in _SIZE_FACT_KEYS]
    if not size_facts:
        return "unknown", CriterionResult("company_size", "No company-size fact found in research.", weight, None)

    conflicting = [f for f in size_facts if f.conflicting]
    if conflicting:
        return "unknown", CriterionResult(
            "company_size", "Company-size evidence conflicts across sources.", weight, _fact_source(conflicting[0])
        )

    target = _norm(criteria.preferred_company_size)
    for f in size_facts:
        if target in _norm(f.text):
            return "matched", CriterionResult(
                "company_size",
                f"Research states: \"{f.text}\" (matches preferred size '{criteria.preferred_company_size}').",
                weight,
                _fact_source(f),
            )
    return "unmatched", CriterionResult(
        "company_size",
        f"Research company-size facts do not match preferred size '{criteria.preferred_company_size}'.",
        weight,
        _fact_source(size_facts[0]),
    )


def _evaluate_business_need(lead: Lead, criteria: CampaignCriteria, need_phrase: str):
    weight = criteria.weights.get("business_needs", DEFAULT_WEIGHTS["business_needs"])
    criterion_name = f"business_need:{need_phrase}"
    if not _research_ready(lead):
        return "unknown", CriterionResult(criterion_name, "No completed research available.", weight, None)

    candidate_facts = [f for f in lead.research.facts if f.category in ("offering", "target_customer", "fact")]
    needle = _norm(need_phrase)
    for f in candidate_facts:
        if needle and needle in _norm(f.text):
            return "matched", CriterionResult(
                criterion_name, f"Research evidence matches business need '{need_phrase}': \"{f.text}\".", weight, _fact_source(f)
            )
    if not candidate_facts:
        return "unknown", CriterionResult(criterion_name, "Research completed but found no relevant facts to check this need.", weight, None)
    return "unmatched", CriterionResult(criterion_name, f"No research evidence found for business need '{need_phrase}'.", weight, None)


def _evaluate_exclusions(lead: Lead, criteria: CampaignCriteria) -> list[dict]:
    triggered = []
    fields_to_check = [("industry", lead.industry), ("country", lead.country)]
    fact_pairs = [(f.text, f) for f in lead.research.facts] if _research_ready(lead) else []

    for phrase in criteria.exclusion_criteria:
        needle = _norm(phrase)
        if not needle:
            continue
        for field_name, value in fields_to_check:
            if value and needle in _norm(value):
                triggered.append(
                    {
                        "criterion": f"exclusion:{phrase}",
                        "detail": f"Lead {field_name} '{value}' matches exclusion phrase '{phrase}'.",
                        "source": _lead_field_source(field_name, value),
                    }
                )
        for text, fact in fact_pairs:
            if needle in _norm(text):
                triggered.append(
                    {
                        "criterion": f"exclusion:{phrase}",
                        "detail": f"Research evidence matches exclusion phrase '{phrase}': \"{text}\".",
                        "source": _fact_source(fact),
                    }
                )
    return triggered


def evaluate_lead(lead: Lead, criteria: CampaignCriteria) -> dict:
    """Pure function: no I/O, no DB writes. Returns the full explainable
    breakdown for one lead against one criteria version."""
    matched, unmatched, unknown = [], [], []

    def _record(result):
        if result is None:
            return
        status, cr = result
        entry = {"criterion": cr.criterion, "detail": cr.detail, "weight": cr.weight, "source": cr.source}
        {"matched": matched, "unmatched": unmatched, "unknown": unknown}[status].append(entry)

    _record(_evaluate_industry(lead, criteria))
    _record(_evaluate_country(lead, criteria))
    _record(_evaluate_company_size(lead, criteria))
    for need in criteria.business_needs:
        _record(_evaluate_business_need(lead, criteria, need))

    exclusions = _evaluate_exclusions(lead, criteria)

    known = matched + unmatched
    total_configured = len(matched) + len(unmatched) + len(unknown)
    evidence_coverage = (len(known) / total_configured) if total_configured else 0.0

    fit_score = None
    if known:
        known_weight = sum(e["weight"] for e in known)
        if known_weight > 0:
            matched_weight = sum(e["weight"] for e in matched)
            fit_score = 100.0 * matched_weight / known_weight

    return {
        "fit_score": fit_score,
        "evidence_coverage": evidence_coverage,
        "matched": matched,
        "unmatched": unmatched,
        "unknown": unknown,
        "exclusions": exclusions,
    }


# --- criteria CRUD --------------------------------------------------------------

def create_criteria(
    db: Session,
    campaign_id: int,
    *,
    product_service: str = "",
    target_industries: Optional[list] = None,
    target_countries: Optional[list] = None,
    preferred_company_size: str = "",
    business_needs: Optional[list] = None,
    exclusion_criteria: Optional[list] = None,
    weights: Optional[dict] = None,
) -> CampaignCriteria:
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise QualificationServiceError(f"Campaign {campaign_id} not found.")

    last_version = (
        db.query(func.max(CampaignCriteria.version)).filter(CampaignCriteria.campaign_id == campaign_id).scalar() or 0
    )

    criteria = CampaignCriteria(
        campaign_id=campaign_id,
        version=last_version + 1,
        product_service=product_service or "",
        target_industries=target_industries or [],
        target_countries=target_countries or [],
        preferred_company_size=preferred_company_size or "",
        business_needs=business_needs or [],
        exclusion_criteria=exclusion_criteria or [],
        weights={**DEFAULT_WEIGHTS, **(weights or {})},
    )
    db.add(criteria)
    db.commit()
    db.refresh(criteria)
    return criteria


def get_current_criteria(db: Session, campaign_id: int) -> Optional[CampaignCriteria]:
    return (
        db.query(CampaignCriteria)
        .filter(CampaignCriteria.campaign_id == campaign_id)
        .order_by(CampaignCriteria.version.desc())
        .first()
    )


# --- qualification (compute + persist) ------------------------------------------

def qualify_lead(db: Session, lead_id: int) -> Qualification:
    lead = db.get(Lead, lead_id)
    if not lead:
        raise QualificationServiceError(f"Lead {lead_id} not found.")

    criteria = get_current_criteria(db, lead.campaign_id)
    if not criteria:
        raise QualificationServiceError("This campaign has no qualification criteria configured yet.")

    result = evaluate_lead(lead, criteria)
    research_version = lead.research.version if lead.research else 0

    q = lead.qualification
    if q is None:
        q = Qualification(lead_id=lead.id)
        db.add(q)

    q.criteria_id = criteria.id
    q.criteria_version = criteria.version
    q.research_version_snapshot = research_version
    q.rubric_version = RUBRIC_VERSION
    q.fit_score = result["fit_score"]
    q.evidence_coverage = result["evidence_coverage"]
    q.matched = result["matched"]
    q.unmatched = result["unmatched"]
    q.unknown = result["unknown"]
    q.exclusions = result["exclusions"]

    db.commit()
    db.refresh(q)
    return q


def qualify_campaign(db: Session, campaign_id: int) -> list[Qualification]:
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise QualificationServiceError(f"Campaign {campaign_id} not found.")
    return [qualify_lead(db, lead.id) for lead in campaign.leads]


def get_qualification(db: Session, lead_id: int) -> Optional[Qualification]:
    lead = db.get(Lead, lead_id)
    if not lead:
        raise QualificationServiceError(f"Lead {lead_id} not found.")
    return lead.qualification


def is_stale(db: Session, qualification: Qualification) -> bool:
    """True if the campaign's criteria or the lead's research have changed
    since this qualification was computed (or the code's scoring rubric
    itself has moved on) -- computed at read time, never stored, so it can
    never drift out of sync with reality."""
    lead = qualification.lead
    current_criteria = get_current_criteria(db, lead.campaign_id)
    current_research_version = lead.research.version if lead.research else 0
    return (
        qualification.rubric_version != RUBRIC_VERSION
        or (current_criteria is not None and qualification.criteria_version != current_criteria.version)
        or qualification.research_version_snapshot != current_research_version
    )


# --- manual labels ---------------------------------------------------------------

def add_label(db: Session, lead_id: int, label: str, notes: Optional[str] = None) -> QualificationLabel:
    if label not in VALID_LABELS:
        raise QualificationServiceError(f"label must be one of {sorted(VALID_LABELS)}, got '{label}'.")
    lead = db.get(Lead, lead_id)
    if not lead:
        raise QualificationServiceError(f"Lead {lead_id} not found.")

    criteria = get_current_criteria(db, lead.campaign_id)
    entry = QualificationLabel(
        lead_id=lead_id,
        label=label,
        notes=(notes or None),
        criteria_version_reviewed=criteria.version if criteria else 0,
        research_version_reviewed=lead.research.version if lead.research else 0,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def get_labels(db: Session, lead_id: int) -> list[QualificationLabel]:
    lead = db.get(Lead, lead_id)
    if not lead:
        raise QualificationServiceError(f"Lead {lead_id} not found.")
    return list(lead.labels)


def get_current_label(db: Session, lead_id: int) -> Optional[QualificationLabel]:
    labels = get_labels(db, lead_id)
    return labels[-1] if labels else None
