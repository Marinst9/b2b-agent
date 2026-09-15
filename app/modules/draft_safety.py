"""Post-generation safety checks for evidence-based drafts. Pure functions,
no I/O -- kept separate from generation/persistence so each is independently
testable.

Two independent layers:

1. `verify_claims` -- every claim's fact_id must reference a ResearchFact
   that actually belongs to the lead's CURRENT research snapshot (the exact
   fact list the model was shown). A fact_id that doesn't resolve there is
   dropped from claim_sources and flagged -- the model could have echoed a
   plausible-looking but wrong/hallucinated id.

2. `scan_for_unsupported_inferences` -- a valid, matching fact_id alone does
   NOT prove the claim's specific wording is actually supported (the model
   could still have added invented specifics beyond what the fact says), so
   the full draft body is separately scanned for categories of invented
   content this system must never produce (funding, growth/staffing figures,
   business problems, prior contact) and flagged unless that exact language
   already appears in one of the lead's own verified facts.
"""
import re

_INVENTION_PATTERNS = {
    "funding_or_investment": re.compile(
        r"\b(raised|funding round|series [a-e]\b|venture capital|invest(?:ed|ment))\b", re.I
    ),
    "growth_or_staffing": re.compile(
        r"\b(grew by|growing team|recently hired|newly hired|doubled|tripled|headcount (?:grew|increased)|expanding rapidly)\b",
        re.I,
    ),
    "business_problem": re.compile(r"\b(struggl(?:e|ing) with|facing challenges|your (?:problem|pain point) with)\b", re.I),
    "previous_contact": re.compile(
        r"\b(as we discussed|following up on our (?:call|conversation|chat)|as mentioned (?:in|during) our (?:call|meeting))\b",
        re.I,
    ),
}


def verify_claims(claims: list, valid_facts: list) -> tuple[list, list]:
    """Returns (verified_claims, review_flags). `valid_facts` must be exactly
    the fact list the model was shown for this generation call."""
    valid_ids = {f.id for f in valid_facts}
    verified, flags = [], []
    for c in claims:
        if c.fact_id not in valid_ids:
            flags.append(
                {
                    "type": "invalid_fact_reference",
                    "detail": f"Claim \"{c.claim}\" cited fact_id={c.fact_id}, which is not part of this lead's "
                    "current research snapshot; the claim was removed.",
                }
            )
            continue
        verified.append(c)
    return verified, flags


def scan_for_unsupported_inferences(body: str, valid_facts: list) -> list:
    combined_fact_text = " ".join(f.text for f in valid_facts).lower()
    flags = []
    for category, pattern in _INVENTION_PATTERNS.items():
        for match in pattern.finditer(body):
            phrase = match.group(0)
            if phrase.lower() not in combined_fact_text:
                flags.append(
                    {
                        "type": "unsupported_inference",
                        "category": category,
                        "detail": f"Draft contains '{phrase}', which is not grounded in any verified fact -- review before sending.",
                    }
                )
    return flags
