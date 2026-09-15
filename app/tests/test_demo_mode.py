import pytest

from modules import email_sender
from demo import fake_providers

# Captured at collection time, before the root conftest's autouse
# `_mock_external_providers` fixture ever runs and monkeypatches
# `email_sender.send_email` module-wide for the whole test session -- these
# tests specifically need the REAL, guarded implementation, not that fake.
_REAL_SEND_EMAIL = email_sender.send_email


@pytest.fixture(autouse=True)
def _restore_demo_mode_env(monkeypatch):
    """Ensures DEMO_MODE never leaks into other tests regardless of outcome."""
    monkeypatch.delenv("DEMO_MODE", raising=False)
    yield


def test_send_email_is_blocked_server_side_when_demo_mode_is_active(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    with pytest.raises(email_sender.DemoModeSendBlockedError):
        _REAL_SEND_EMAIL("someone@example.com", "Subject", "Body")


def test_send_email_works_normally_when_demo_mode_is_not_set(monkeypatch):
    """Confirms the guard is genuinely conditional -- with DEMO_MODE unset,
    execution proceeds PAST the guard into the real SMTP attempt, instead of
    being blocked. The SMTP call itself is stubbed to fail deterministically
    (no real network access from a test), so this only asserts the guard
    didn't fire -- not real send success (this repo's configured Mailtrap
    sandbox would otherwise actually accept the message)."""
    monkeypatch.delenv("DEMO_MODE", raising=False)

    class _ExplodingSMTP:
        def __init__(self, *a, **k):
            raise ConnectionRefusedError("no real SMTP server in this test")

    monkeypatch.setattr(email_sender.smtplib, "SMTP", _ExplodingSMTP)
    result = _REAL_SEND_EMAIL("someone@example.com", "Subject", "Body")
    assert result is False  # the real function catches the connection failure and returns False, not DemoModeSendBlockedError


def test_fake_fetch_url_returns_fixture_content_for_a_known_website():
    page = fake_providers._fake_fetch_url("https://northbridgeledger.example/")
    assert "Northbridge Ledger" in page.text


def test_fake_fetch_url_returns_placeholder_for_an_unknown_website():
    page = fake_providers._fake_fetch_url("https://not-a-fixture-domain.example/")
    assert "No demo fixture content" in page.text


def test_fake_extract_facts_returns_the_fixtures_expected_facts():
    result = fake_providers._fake_extract_facts("ignored", "https://northbridgeledger.example/")
    assert len(result.facts) == 3
    assert any(f.category == "offering" for f in result.facts)


def test_fake_generate_draft_cites_a_real_fact_id_from_the_facts_argument():
    facts = [{"id": 42, "category": "offering", "key": None, "text": "Sells widgets"}]
    result = fake_providers._fake_generate_draft(
        company="Acme", contact_name="Jane", product_service="our tool", facts=facts, language="en", tone="professional", length="medium",
    )
    assert result.claims[0].fact_id == 42


def test_fake_generate_draft_falls_back_to_generic_with_no_facts():
    result = fake_providers._fake_generate_draft(
        company="Acme", contact_name="Jane", product_service="our tool", facts=[], language="en", tone="professional", length="medium",
    )
    assert result.claims == []  # no facts to cite -- must not fabricate personalization
    assert result.body


def test_blocked_send_email_always_raises():
    with pytest.raises(email_sender.DemoModeSendBlockedError):
        fake_providers._blocked_send_email("a@b.example", "s", "m")
