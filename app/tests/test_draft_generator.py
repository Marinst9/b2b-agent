import json

import pytest

from modules import draft_generator
from modules.draft_generator import GeneratedDraftResult, DraftGenerationError


def make_fake_call_model(response_dict, usage=None):
    def _fake(prompt):
        return json.dumps(response_dict), (usage or {})

    return _fake


def test_generate_draft_returns_validated_result_with_usage():
    call_model = make_fake_call_model(
        {"subject": "Hello Acme", "body": "We help companies like yours.", "claims": [{"claim": "x", "fact_id": 1}]},
        usage={"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
    )
    result = draft_generator.generate_draft(
        company="Acme",
        contact_name="Jane",
        product_service="B2B analytics",
        facts=[{"id": 1, "category": "offering", "key": None, "text": "Sells widgets"}],
        call_model=call_model,
    )
    assert isinstance(result, GeneratedDraftResult)
    assert result.subject == "Hello Acme"
    assert len(result.claims) == 1
    assert result.claims[0].fact_id == 1
    assert result.usage["total_tokens"] == 12
    assert result.model == draft_generator.MODEL


def test_generate_draft_retries_on_invalid_json_then_succeeds():
    calls = {"n": 0}

    def flaky(prompt):
        calls["n"] += 1
        if calls["n"] < 2:
            return "not json {{{", {}
        return json.dumps({"subject": "S", "body": "B", "claims": []}), {}

    result = draft_generator.generate_draft(
        company="Acme", contact_name="Jane", product_service="X", facts=[], call_model=flaky
    )
    assert result.subject == "S"
    assert calls["n"] == 2


def test_generate_draft_raises_after_exhausting_retries():
    def always_broken(prompt):
        return "{not valid json", {}

    with pytest.raises(DraftGenerationError):
        draft_generator.generate_draft(company="Acme", contact_name="Jane", product_service="X", facts=[], call_model=always_broken)


def test_generate_draft_rejects_missing_required_fields():
    def missing_body(prompt):
        return json.dumps({"subject": "S", "claims": []}), {}

    with pytest.raises(DraftGenerationError):
        draft_generator.generate_draft(company="Acme", contact_name="Jane", product_service="X", facts=[], call_model=missing_body)


def test_prompt_includes_only_provided_facts_and_language_tone_length():
    captured = {}

    def capturing(prompt):
        captured["prompt"] = prompt
        return json.dumps({"subject": "S", "body": "B", "claims": []}), {}

    draft_generator.generate_draft(
        company="Acme",
        contact_name="Jane",
        product_service="B2B analytics",
        facts=[{"id": 42, "category": "offering", "key": None, "text": "Sells widgets"}],
        language="mk",
        tone="friendly",
        length="short",
        call_model=capturing,
    )
    prompt = captured["prompt"]
    assert "id=42" in prompt
    assert "Sells widgets" in prompt
    assert "Macedonian" in prompt
    assert "friendly" in prompt


def test_provider_exception_is_treated_as_a_failed_attempt_not_a_crash():
    calls = {"n": 0}

    def flaky_provider(prompt):
        calls["n"] += 1
        if calls["n"] < 2:
            raise ConnectionError("provider down")
        return json.dumps({"subject": "S", "body": "B", "claims": []}), {}

    result = draft_generator.generate_draft(company="Acme", contact_name="Jane", product_service="X", facts=[], call_model=flaky_provider)
    assert result.subject == "S"


def test_provider_exception_exhausting_retries_raises_draft_generation_error():
    def always_down(prompt):
        raise ConnectionError("provider down")

    with pytest.raises(DraftGenerationError):
        draft_generator.generate_draft(company="Acme", contact_name="Jane", product_service="X", facts=[], call_model=always_down)
