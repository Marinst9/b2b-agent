import os
from pathlib import Path

# Point the app at an isolated, file-based SQLite database for the whole test
# session, before any test module imports `database`/`api`/`services`. Tests
# must never touch the real DATABASE_URL from .env.
TEST_DB_PATH = Path(__file__).resolve().parent / "_test.db"
if TEST_DB_PATH.exists():
    TEST_DB_PATH.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"

# No real broker runs in this environment (see tests/integration/README.md).
# Tests that create Job rows deliberately exercise the "broker unreachable"
# branch of durable dispatch -- keep that failure fast so the suite doesn't
# spend minutes hitting Redis's default reconnect timeouts.
os.environ.setdefault("CELERY_BROKER_CONNECT_TIMEOUT_SECONDS", "0.2")
os.environ.setdefault("CELERY_BROKER_URL", "redis://127.0.0.1:6399/0")  # unused test port, fails fast (no DNS lookup)

import pytest
from fastapi.testclient import TestClient

import database
import api
from modules import enrichment, email_sender, draft_generator


@pytest.fixture(autouse=True)
def _reset_db():
    database.Base.metadata.drop_all(bind=database.engine)
    database.Base.metadata.create_all(bind=database.engine)
    yield


@pytest.fixture(autouse=True)
def _mock_external_providers(monkeypatch):
    """Never let a test hit OpenAI, Hunter, or a real SMTP server.

    draft_generator is mocked at its lowest seam (`_call_openai`, the actual
    network call), NOT at the top-level `generate_draft` function -- that way
    a test can still call `draft_generator.generate_draft(..., call_model=...)`
    directly to unit-test the real retry/validation logic, while any test
    that goes through the API/draft_service without specifying its own
    call_model safely gets this generic fake instead of hitting OpenAI.
    """

    def fake_get_email(first_name, last_name, domain):
        return {"email": None, "score": 0}

    def fake_send_email(to_email, subject, message):
        return True

    def fake_call_openai(prompt):
        import json

        return (
            json.dumps({"subject": "Test subject", "body": "Test body", "claims": []}),
            {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        )

    monkeypatch.setattr(enrichment, "get_email", fake_get_email)
    monkeypatch.setattr(email_sender, "send_email", fake_send_email)
    monkeypatch.setattr(draft_generator, "_call_openai", fake_call_openai)


@pytest.fixture()
def client():
    with TestClient(api.app) as c:
        yield c
