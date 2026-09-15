"""Runs the drafting comparison (original basic prompt vs the current
evidence-based workflow) over the synthetic dataset, in either:

- "mock" mode (default): a deterministic, clearly-labeled fake model call.
  Verifies the harness itself end-to-end (data flows through both
  generators, metrics compute, results persist) -- it does NOT measure real
  model quality, and latency/token fields are left unset (None) rather than
  filled with meaningless numbers from a fake call.
- "live" mode (opt-in only, see evaluation/run_eval.py): real OpenAI calls
  through the exact same `ai_generator`/`draft_generator` modules production
  uses, capped by an explicit `max_calls` budget the caller must supply.

Both approaches are run against the SAME case, campaign offering, language,
tone, and length -- but note `ai_generator.generate_message` has no
language/tone/length parameters at all (it hardcodes a Macedonian prompt).
That asymmetry is a real difference between the two approaches, not a
harness bug, and is recorded in each EvalRun rather than papered over.
"""
import json
import time
from dataclasses import dataclass
from typing import Optional

from modules import ai_generator, draft_generator
from evaluation.dataset import EvalCase, EvalDataset
from evaluation import metrics

APPROACHES = ("basic", "evidence_based")


@dataclass
class GenerationOutcome:
    approach: str
    subject: Optional[str]
    body: Optional[str]
    claims: list
    raw_output: dict
    error: Optional[str]
    latency_ms: Optional[float]
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    total_tokens: Optional[int]
    metrics: metrics.DeterministicMetrics


def _mock_basic_call_model(case: EvalCase) -> str:
    if "missing_info" in case.tags:
        return "[MOCK] Здраво, само кратка честитка без конкретни детали за компанијата."
    return f"[MOCK] Здраво {case.contact_name or ''}, забележавме дека {case.company} работи во индустријата {case.industry or 'непозната'}. Дали имате 15 минути за кратка средба?"


def _mock_evidence_call_model(case: EvalCase):
    claims = []
    body_extra = ""
    if case.expected_facts:
        claims = [{"claim": case.expected_facts[0].text, "fact_id": 0}]
        body_extra = f" {case.expected_facts[0].text}."
    if "unsupported_claim_bait" in case.tags:
        # Deliberately injects invented language the mock harness's OWN
        # metrics should catch -- proves check_unsupported_claims actually
        # fires, without needing a real (and non-deterministic) model call.
        body_extra += " We heard you recently raised a funding round."
    subject = f"[MOCK] Helping {case.company}"
    body = f"[MOCK] Hi {case.contact_name or 'there'}, we help companies like {case.company}.{body_extra}"
    return json.dumps({"subject": subject, "body": body, "claims": claims}), {}


def _run_basic(case: EvalCase, *, mode: str) -> GenerationOutcome:
    call_model = (lambda prompt: _mock_basic_call_model(case)) if mode == "mock" else None
    lead = {"name": case.contact_name, "company": case.company, "industry": case.industry, "country": case.country}

    start = time.perf_counter() if mode == "live" else None
    try:
        result = ai_generator.generate_draft(lead, call_model=call_model)
        elapsed_ms = (time.perf_counter() - start) * 1000 if start is not None else None
        m = metrics.compute_metrics(
            case=case,
            approach="basic",
            subject=result["subject"],
            body=result["body"],
            claims=[],
            language="mk",  # ai_generator hardcodes Macedonian -- not a configurable parameter
            length="medium",  # ai_generator hardcodes "max 5 sentences" -- approximated as medium
            schema_valid=bool(result.get("subject")) and bool(result.get("body")),
            schema_note="ai_generator has no structured schema beyond non-empty subject/body -- it returns free text, not JSON.",
        )
        return GenerationOutcome(
            approach="basic",
            subject=result["subject"],
            body=result["body"],
            claims=[],
            raw_output=result,
            error=None,
            latency_ms=round(elapsed_ms, 1) if elapsed_ms is not None else None,
            prompt_tokens=None,  # ai_generator's basic path never reads response.usage
            completion_tokens=None,
            total_tokens=None,
            metrics=m,
        )
    except Exception as e:
        empty_metrics = metrics.compute_metrics(
            case=case, approach="basic", subject="", body="", claims=[], language="mk", length="medium",
            schema_valid=False, schema_note=f"generation raised: {e}",
        )
        return GenerationOutcome(
            approach="basic", subject=None, body=None, claims=[], raw_output={}, error=str(e),
            latency_ms=None, prompt_tokens=None, completion_tokens=None, total_tokens=None, metrics=empty_metrics,
        )


def _run_evidence_based(case: EvalCase, *, mode: str, language: str, tone: str, length: str, offering: str) -> GenerationOutcome:
    call_model = (lambda prompt: _mock_evidence_call_model(case)) if mode == "mock" else None
    facts = [{"id": i, "category": f.category, "text": f.text} for i, f in enumerate(case.expected_facts)]

    start = time.perf_counter() if mode == "live" else None
    try:
        result = draft_generator.generate_draft(
            company=case.company,
            contact_name=case.contact_name,
            product_service=offering,
            facts=facts,
            language=language,
            tone=tone,
            length=length,
            call_model=call_model,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000 if start is not None else None
        claims_as_dicts = [{"claim": c.claim, "fact_id": c.fact_id} for c in result.claims]
        m = metrics.compute_metrics(
            case=case, approach="evidence_based", subject=result.subject, body=result.body,
            claims=result.claims, language=language, length=length,
            schema_valid=True, schema_note="Parsed and validated against draft_generator.GeneratedDraft.",
        )
        usage = result.usage or {}
        return GenerationOutcome(
            approach="evidence_based",
            subject=result.subject,
            body=result.body,
            claims=claims_as_dicts,
            raw_output={"subject": result.subject, "body": result.body, "claims": claims_as_dicts, "model": result.model},
            error=None,
            latency_ms=round(elapsed_ms, 1) if elapsed_ms is not None else None,
            prompt_tokens=usage.get("prompt_tokens") if mode == "live" else None,
            completion_tokens=usage.get("completion_tokens") if mode == "live" else None,
            total_tokens=usage.get("total_tokens") if mode == "live" else None,
            metrics=m,
        )
    except Exception as e:
        empty_metrics = metrics.compute_metrics(
            case=case, approach="evidence_based", subject="", body="", claims=[], language=language, length=length,
            schema_valid=False, schema_note=f"generation raised: {e}",
        )
        return GenerationOutcome(
            approach="evidence_based", subject=None, body=None, claims=[], raw_output={}, error=str(e),
            latency_ms=None, prompt_tokens=None, completion_tokens=None, total_tokens=None, metrics=empty_metrics,
        )


def run(
    dataset: EvalDataset,
    *,
    mode: str = "mock",
    language: str = "en",
    tone: str = "professional",
    length: str = "medium",
    max_calls: Optional[int] = None,
    case_ids: Optional[list[str]] = None,
) -> list[tuple[EvalCase, GenerationOutcome]]:
    """Runs both approaches over every case (or just `case_ids`, if given).

    In live mode, `max_calls` is a hard budget on the number of real model
    calls made (basic + evidence_based counted together) -- the run stops
    partway through the dataset once the budget is spent rather than ever
    silently exceeding it. In mock mode `max_calls` is ignored (no real
    calls are ever made). Returns a list of (case, outcome) pairs, two per
    case (one per approach) unless the budget cut the run short.
    """
    if mode not in ("mock", "live"):
        raise ValueError(f"mode must be 'mock' or 'live', got {mode!r}")
    if mode == "live" and not max_calls:
        raise ValueError("live mode requires an explicit max_calls budget greater than 0.")

    cases = [c for c in dataset.cases if case_ids is None or c.id in case_ids]
    outcomes: list[tuple[EvalCase, GenerationOutcome]] = []
    calls_made = 0
    for case in cases:
        for approach in APPROACHES:
            if mode == "live" and calls_made >= max_calls:
                return outcomes
            if approach == "basic":
                outcome = _run_basic(case, mode=mode)
            else:
                outcome = _run_evidence_based(
                    case, mode=mode, language=language, tone=tone, length=length, offering=dataset.campaign_offering
                )
            if mode == "live":
                calls_made += 1
            outcomes.append((case, outcome))
    return outcomes
