"""Company research orchestration: crawl -> extract -> validate -> persist.

Kept separate from services.py (drafts/sending) on purpose -- this module
never calls ai_generator.generate_draft, email_sender.send_email, or any
approval function, and has no code path that could send an email or change a
draft's approval state.
"""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from database import Lead, CompanyResearch, ResearchFact
from modules import web_fetcher, html_utils, research_extractor, research_validation

DEFAULT_PAGE_BUDGET = 5
MAX_PAGE_BUDGET = 10
DEFAULT_CACHE_TTL_SECONDS = 24 * 60 * 60
_INFORMATIVE_KEYWORDS = ("about", "product", "service", "solution", "customer", "pricing", "company", "team")


class ResearchServiceError(Exception):
    """Raised for request-level errors that should map to a 4xx HTTP response."""


def _normalize_website_url(website: str) -> str:
    website = website.strip()
    if not website:
        raise ResearchServiceError("No website supplied.")
    if not urlsplit(website).scheme:
        website = f"https://{website}"
    if not urlsplit(website).path:
        website = f"{website}/"
    return website


def _score_link(link: str) -> int:
    path = urlsplit(link).path.lower()
    return 0 if any(k in path for k in _INFORMATIVE_KEYWORDS) else 1


def crawl_site(base_url: str, page_budget: int, fetch_fn=None) -> list:
    """Fetches the homepage plus up to (page_budget - 1) same-site pages found
    via links on the homepage. Homepage failures propagate; secondary-page
    failures are skipped (not fatal to the whole research run)."""
    fetch_fn = fetch_fn or web_fetcher.fetch_url
    homepage = fetch_fn(base_url)
    pages = [homepage]

    if page_budget <= 1:
        return pages

    links = html_utils.extract_links(homepage.text, homepage.url)
    same_site_links = [l for l in links if html_utils.same_site(l, homepage.url)]

    seen = {homepage.url}
    deduped = []
    for link in same_site_links:
        if link not in seen:
            seen.add(link)
            deduped.append(link)
    candidates = sorted(deduped, key=_score_link)

    for link in candidates:
        if len(pages) >= page_budget:
            break
        try:
            pages.append(fetch_fn(link))
        except (web_fetcher.UnsafeURLError, web_fetcher.FetchError):
            continue

    return pages


def _get_or_create_stub(db: Session, lead: Lead) -> CompanyResearch:
    if lead.research is None:
        lead.research = CompanyResearch(lead_id=lead.id, website=lead.website or "", status="pending")
        db.add(lead.research)
    return lead.research


def run_research(
    db: Session,
    lead_id: int,
    *,
    force_refresh: bool = False,
    page_budget: int = DEFAULT_PAGE_BUDGET,
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    fetch_fn=None,
    extract_fn=None,
) -> CompanyResearch:
    fetch_fn = fetch_fn or web_fetcher.fetch_url
    extract_fn = extract_fn or research_extractor.extract_facts
    lead = db.get(Lead, lead_id)
    if not lead:
        raise ResearchServiceError(f"Lead {lead_id} not found.")

    page_budget = max(1, min(page_budget, MAX_PAGE_BUDGET))
    research = _get_or_create_stub(db, lead)

    if not lead.website or not lead.website.strip():
        research.status = "needs_website"
        research.error = None
        research.website = ""
        db.commit()
        db.refresh(research)
        return research

    now = datetime.now(timezone.utc)
    expires_at = research.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        # SQLite (used in tests) doesn't persist tz-awareness on DateTime
        # columns the way Postgres does; values stored here are always UTC.
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if not force_refresh and research.status == "completed" and expires_at is not None and expires_at > now:
        return research  # cache hit -- no network calls

    had_prior_success = research.status == "completed"

    research.status = "in_progress"
    research.website = lead.website
    research.started_at = now
    db.commit()

    try:
        base_url = _normalize_website_url(lead.website)
        pages = crawl_site(base_url, page_budget, fetch_fn=fetch_fn)

        page_texts = {p.url: html_utils.extract_visible_text(p.text) for p in pages}

        all_facts = []
        for page in pages:
            result = extract_fn(page_texts[page.url], page.url)
            all_facts.extend(result.facts)

        verified, _rejected = research_validation.verify_facts(all_facts, page_texts)
        conflicting_keys = research_validation.detect_conflicting_keys(verified)

        for existing_fact in list(research.facts):
            db.delete(existing_fact)

        retrieved_at = datetime.now(timezone.utc)
        for fact in verified:
            db.add(
                ResearchFact(
                    research_id=research.id,
                    category=fact.category,
                    key=fact.key,
                    text=fact.text,
                    excerpt=fact.excerpt,
                    source_url=fact.source_url,
                    retrieved_at=retrieved_at,
                    conflicting=bool(fact.key and fact.key in conflicting_keys),
                )
            )

        research.status = "completed"
        research.error = None
        research.version = (research.version or 0) + 1
        research.pages_fetched = len(pages)
        research.offerings_unknown = not any(f.category == "offering" for f in verified)
        research.target_customers_unknown = not any(f.category == "target_customer" for f in verified)
        research.has_conflicts = bool(conflicting_keys)
        research.completed_at = retrieved_at
        research.expires_at = retrieved_at + timedelta(seconds=cache_ttl_seconds)
        research.last_refresh_error = None
        research.last_refresh_attempted_at = None

    except Exception as e:
        if had_prior_success:
            # Preserve the previous successful research; only record that a
            # refresh attempt failed.
            research.status = "completed"
            research.last_refresh_error = str(e)
            research.last_refresh_attempted_at = datetime.now(timezone.utc)
        else:
            research.status = "failed"
            research.error = str(e)
            research.pages_fetched = 0

    db.commit()
    db.refresh(research)
    return research


def get_research(db: Session, lead_id: int) -> CompanyResearch:
    lead = db.get(Lead, lead_id)
    if not lead:
        raise ResearchServiceError(f"Lead {lead_id} not found.")
    if lead.research is None:
        return CompanyResearch(lead_id=lead_id, website=lead.website or "", status="pending", facts=[])
    return lead.research
