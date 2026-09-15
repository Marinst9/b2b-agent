"""Fixture-backed fake external providers for demo mode (see run_demo.py).

Reuses the SAME synthetic dataset as app/evaluation/, so the demo and the
offline evaluation harness share one source of truth about what "Northbridge
Ledger Co" or "Vantage Cloud Systems" are. `apply()` must be called before
the FastAPI app / Celery are imported anywhere that matters (see
run_demo.py) -- it is never imported by production code, and never active
unless a script explicitly calls it.

Matched by website substring, so importing a CSV with any of the fixture
company websites (see demo/demo_leads.csv, generated from the same dataset)
drives a fully offline, deterministic research -> qualify -> draft flow with
no real network or LLM calls.
"""
from modules import web_fetcher, research_extractor, draft_generator, email_sender
from modules.research_extractor import ExtractionResult, ExtractedFact
from modules.draft_generator import GeneratedDraftResult, DraftClaim
from evaluation.dataset import load_dataset

_dataset = load_dataset()


def _case_for_url(url: str):
    if not url:
        return None
    url_lower = url.lower()
    for case in _dataset.cases:
        if case.website and case.website.lower() in url_lower:
            return case
    return None


class _FakePage:
    def __init__(self, url, text):
        self.url = url
        self.status_code = 200
        self.content_type = "text/html"
        self.text = text


def _fake_fetch_url(url, **kwargs):
    case = _case_for_url(url)
    excerpt = case.source_excerpt if case and case.source_excerpt else "No demo fixture content available for this URL."
    return _FakePage(url, f"<html><body>{excerpt}</body></html>")


def _fake_extract_facts(page_text, source_url, **kwargs):
    case = _case_for_url(source_url)
    if not case:
        return ExtractionResult(facts=[])
    return ExtractionResult(
        facts=[
            ExtractedFact(category=f.category, key=f.key, text=f.text, excerpt=f.text.lower(), source_url=source_url)
            for f in case.expected_facts
        ]
    )


def _fake_generate_draft(*, company, contact_name, product_service, facts, language, tone, length, call_model=None):
    """Mirrors draft_generator.generate_draft's real signature and return
    type exactly, so draft_service's downstream claim-verification/
    unsupported-inference checks run for real against this fake output --
    only the model call itself is faked. `facts` here are the REAL
    already-persisted fact dicts (with real database ids) built by
    draft_service, not the fixture's own case data."""
    if not facts:
        return GeneratedDraftResult(
            subject=f"Reaching out to {company}",
            body=f"Hi {contact_name or 'there'}, we help companies with {product_service or 'their outreach'}. Would you be open to a short call?",
            claims=[],
            model="demo-fixture",
            usage={},
        )
    lead_fact = facts[0]
    return GeneratedDraftResult(
        subject=f"Helping {company} with {product_service or 'outreach'}",
        body=(
            f"Hi {contact_name or 'there'}, we noticed that {lead_fact['text'].lower()}. "
            f"We help companies like {company} with {product_service or 'their outreach'}. "
            "Would you have 15 minutes this week for a short call?"
        ),
        claims=[DraftClaim(claim=lead_fact["text"], fact_id=lead_fact["id"])],
        model="demo-fixture",
        usage={},
    )


def _blocked_send_email(to_email, subject, message):
    # email_sender.send_email already self-guards via the DEMO_MODE env var
    # (see modules/email_sender.py) -- this monkeypatch is defense-in-depth
    # specific to the demo process, not the only thing standing between
    # demo mode and a real send.
    raise email_sender.DemoModeSendBlockedError("Demo mode: real email sending is disabled.")


def apply():
    web_fetcher.fetch_url = _fake_fetch_url
    research_extractor.extract_facts = _fake_extract_facts
    draft_generator.generate_draft = _fake_generate_draft
    email_sender.send_email = _blocked_send_email
