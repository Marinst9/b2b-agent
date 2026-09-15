"""Deterministic, non-LLM quality checks for a single generated draft.

Explicitly NOT a measure of semantic correctness -- source-reference
validity here means "the cited fact_id resolves to a real fact in this
case's evidence," and unsupported-claims detection is the same regex
heuristic already used in production (modules/draft_safety.py). Both are
useful, cheap signals, and both can be fooled: a citation can point at a
real fact that doesn't actually support the specific wording of the claim,
and the regex only catches phrasings it already knows about. Treat these as
one input among several (see app/evaluation/harness.py's ModelJudgeResult
for the optional, explicitly-labeled LLM-judge layer, and the human_rating
fields for the only layer that can assess real relevance/personalization).
"""
import re
from dataclasses import dataclass, field
from typing import Optional

from modules import draft_safety
from evaluation.dataset import EvalCase, EvalFact

# Heuristic only: counts Cyrillic vs Latin letters. Real language ID would
# use a proper library (e.g. langdetect/fasttext) -- this is a cheap,
# dependency-free proxy that is good enough to catch "wrong alphabet
# entirely" but not to validate grammar or dialect.
_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")
_LATIN_LETTER_RE = re.compile(r"[A-Za-z]")

# Heuristic only: naive sentence count via terminal punctuation. Mirrors the
# same bucket boundaries as draft_generator.LENGTH_GUIDANCE, with generous
# tolerance since sentence-splitting on punctuation alone is imprecise.
_LENGTH_BOUNDS = {
    "short": (1, 4),
    "medium": (2, 8),
    "long": (5, 14),
}
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+")


@dataclass
class LanguageCheck:
    expected: str
    method: str
    cyrillic_ratio: float
    compliant: bool


@dataclass
class LengthCheck:
    expected: str
    method: str
    sentence_count: int
    compliant: bool


@dataclass
class SourceRefCheck:
    applicable: bool
    verified_count: int
    invalid_count: int
    flags: list


@dataclass
class UnsupportedClaimsCheck:
    method: str
    flags: list
    count: int


@dataclass
class DeterministicMetrics:
    schema_valid: bool
    schema_note: str
    source_refs: SourceRefCheck
    language: LanguageCheck
    length: LengthCheck
    unsupported_claims: UnsupportedClaimsCheck


def _fake_fact_objects(facts: list[EvalFact]):
    """draft_safety.verify_claims/scan_for_unsupported_inferences expect
    objects with .id/.text (as ResearchFact rows would provide) -- fixtures
    are plain dataclasses with no database id, so this assigns stable
    positional ids for the duration of one metrics computation."""

    class _Fact:
        def __init__(self, id_, text):
            self.id = id_
            self.text = text

    return [_Fact(i, f.text) for i, f in enumerate(facts)]


def check_language(body: str, language: str) -> LanguageCheck:
    cyrillic = len(_CYRILLIC_RE.findall(body))
    latin = len(_LATIN_LETTER_RE.findall(body))
    total = cyrillic + latin
    ratio = (cyrillic / total) if total else 0.0
    if language == "mk":
        compliant = ratio >= 0.5
    else:
        compliant = ratio < 0.5
    return LanguageCheck(expected=language, method="cyrillic_letter_ratio_heuristic", cyrillic_ratio=round(ratio, 3), compliant=compliant)


def check_length(body: str, length: str) -> LengthCheck:
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(body) if s.strip()]
    count = len(sentences)
    lo, hi = _LENGTH_BOUNDS.get(length, (1, 20))
    return LengthCheck(expected=length, method="sentence_count_heuristic", sentence_count=count, compliant=lo <= count <= hi)


def check_source_refs(claims: list, case: EvalCase, applicable: bool) -> SourceRefCheck:
    if not applicable or not case.expected_facts:
        return SourceRefCheck(applicable=False, verified_count=0, invalid_count=0, flags=[])
    fact_objs = _fake_fact_objects(case.expected_facts)
    verified, flags = draft_safety.verify_claims(claims, fact_objs)
    return SourceRefCheck(applicable=True, verified_count=len(verified), invalid_count=len(flags), flags=flags)


def check_unsupported_claims(body: str, case: EvalCase) -> UnsupportedClaimsCheck:
    fact_objs = _fake_fact_objects(case.expected_facts)
    flags = draft_safety.scan_for_unsupported_inferences(body, fact_objs)
    return UnsupportedClaimsCheck(method="draft_safety_regex_heuristic_v1", flags=flags, count=len(flags))


def compute_metrics(
    *,
    case: EvalCase,
    approach: str,
    subject: str,
    body: str,
    claims: Optional[list],
    language: str,
    length: str,
    schema_valid: bool,
    schema_note: str,
) -> DeterministicMetrics:
    """`approach` is "basic" or "evidence_based". Source-reference checking
    only applies to evidence_based (the basic prompt has no citation
    concept at all) -- reported as not-applicable, never as a failure, for
    the basic approach."""
    return DeterministicMetrics(
        schema_valid=schema_valid,
        schema_note=schema_note,
        source_refs=check_source_refs(claims or [], case, applicable=(approach == "evidence_based")),
        language=check_language(body, language),
        length=check_length(body, length),
        unsupported_claims=check_unsupported_claims(body, case),
    )
