"""Fake external-provider implementations applied INSIDE the real,
containerized Celery worker (see container_entrypoint.py and
docker-compose.integration.yml's worker-integration service).

Why this exists as a separate module from tests/conftest.py's
`_mock_external_providers` fixture: that fixture uses `monkeypatch`, which
only ever affects the SAME Python process it runs in (the pytest process,
via the in-process `client` TestClient). These integration tests exercise a
genuinely separate Linux worker process (`worker-integration`), so mocking
has to be applied INSIDE that process, before it starts consuming tasks --
monkeypatching from the test side cannot reach across the process boundary.

Never hits a real website, LLM, or SMTP server. Test-only: not imported by
any production code path or by docker-compose.yml's plain worker service.
"""
import os

from modules import web_fetcher, research_extractor, draft_generator, email_sender
from modules.research_extractor import ExtractionResult, ExtractedFact
from modules.draft_generator import GeneratedDraftResult

# Lead `company` values starting with this prefix trigger deliberately-flaky
# behavior in `_fake_generate_draft`, keyed by a caller-supplied unique id so
# concurrent/repeated test runs against the same long-lived Redis don't share
# counters: "FLAKY:<fail_count>:<unique_id>".
FLAKY_PREFIX = "FLAKY:"


class _FakePage:
    def __init__(self, url, text):
        self.url = url
        self.status_code = 200
        self.content_type = "text/html"
        self.text = text


def _fake_fetch_url(url, **kwargs):
    return _FakePage(
        url,
        "<html><body>Fake integration-test company page. Builds outreach "
        "automation software for mid-size B2B teams.</body></html>",
    )


def _fake_extract_facts(page_text, source_url, **kwargs):
    return ExtractionResult(
        facts=[
            ExtractedFact(
                category="offering",
                key=None,
                text="Builds outreach automation software",
                excerpt="builds outreach automation software",
                source_url=source_url,
            )
        ]
    )


def _redis_client():
    import redis

    broker_url = os.environ.get("CELERY_BROKER_URL", "redis://redis-integration:6379/0")
    return redis.Redis.from_url(broker_url)


def _fake_generate_draft(*, company, contact_name, product_service, facts, language, tone, length, call_model=None):
    if company.startswith(FLAKY_PREFIX):
        _, n_str, unique_id = company.split(":", 2)
        n = int(n_str)
        key = f"integration-fake:flaky-draft:{unique_id}"
        client = _redis_client()
        count = client.incr(key)
        client.expire(key, 600)
        if count <= n:
            raise ConnectionError(f"simulated transient blip (attempt {count} of {n})")

    return GeneratedDraftResult(
        subject=f"Draft for {company}",
        body=f"Hi {contact_name}, we help teams like {company} with outreach automation.",
        claims=[],
        model="fake-integration-model",
        usage={"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    )


def _fake_send_email(to_email, subject, message):
    raise AssertionError("REAL EMAIL SEND ATTEMPTED from the integration worker -- this must never happen")


def apply():
    web_fetcher.fetch_url = _fake_fetch_url
    research_extractor.extract_facts = _fake_extract_facts
    draft_generator.generate_draft = _fake_generate_draft
    email_sender.send_email = _fake_send_email
