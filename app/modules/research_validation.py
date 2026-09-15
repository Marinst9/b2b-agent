"""Post-extraction validation: verifies each fact's excerpt against the actual
fetched page text (rejecting unsupported/fabricated facts) and flags
conflicting evidence among same-key singular facts. Pure functions, no I/O --
kept separate from fetching/extraction/persistence so each is independently
testable.
"""
from modules.research_extractor import ExtractedFact


def _normalize(text: str) -> str:
    return " ".join((text or "").split()).lower()


def verify_facts(
    facts: list[ExtractedFact], page_texts: dict[str, str]
) -> tuple[list[ExtractedFact], list[ExtractedFact]]:
    """Splits `facts` into (verified, rejected). A fact is verified only if its
    excerpt occurs verbatim (whitespace/case-insensitive) in the text fetched
    for its own declared source_url."""
    verified, rejected = [], []
    for fact in facts:
        page_text = page_texts.get(fact.source_url)
        excerpt = _normalize(fact.excerpt)
        if page_text is not None and excerpt and excerpt in _normalize(page_text):
            verified.append(fact)
        else:
            rejected.append(fact)
    return verified, rejected


def detect_conflicting_keys(facts: list[ExtractedFact]) -> set[str]:
    """Returns the set of `key` values (category == "fact") for which at least
    two facts disagree on `text`."""
    by_key: dict[str, set[str]] = {}
    for fact in facts:
        if fact.category == "fact" and fact.key:
            by_key.setdefault(fact.key, set()).add(_normalize(fact.text))

    return {key for key, texts in by_key.items() if len(texts) > 1}
