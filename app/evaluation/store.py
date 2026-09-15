"""Persists harness.run() output into the EvalRun/EvalResult tables (see
database.py) -- separate from Lead/QualificationLabel, per this feature's
requirement to keep evaluation fixtures and results out of real-lead and
human-labeled-ML data.
"""
from dataclasses import asdict

from sqlalchemy.orm import Session

import database
from evaluation.dataset import EvalDataset
from modules import ai_generator, draft_generator


def save_run(
    db: Session,
    dataset: EvalDataset,
    outcomes: list,
    *,
    mode: str,
    language: str,
    tone: str,
    length: str,
    max_calls: int = None,
    notes: str = "",
) -> database.EvalRun:
    calls_made = len(outcomes) if mode == "live" else 0
    run = database.EvalRun(
        dataset_version=dataset.version,
        mode=mode,
        model_basic=ai_generator.MODEL,
        model_evidence_based=draft_generator.MODEL,
        prompt_version_basic=ai_generator.PROMPT_VERSION,
        prompt_version_evidence_based=draft_generator.PROMPT_VERSION,
        language=language,
        tone=tone,
        length=length,
        max_calls=max_calls,
        calls_made=calls_made,
        notes=notes,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    for case, outcome in outcomes:
        m = outcome.metrics
        result = database.EvalResult(
            eval_run_id=run.id,
            case_id=case.id,
            case_tags=case.tags,
            approach=outcome.approach,
            subject=outcome.subject,
            body=outcome.body,
            raw_output=outcome.raw_output,
            error=outcome.error,
            latency_ms=outcome.latency_ms,
            prompt_tokens=outcome.prompt_tokens,
            completion_tokens=outcome.completion_tokens,
            total_tokens=outcome.total_tokens,
            metrics={
                "schema_valid": m.schema_valid,
                "schema_note": m.schema_note,
                "source_refs": asdict(m.source_refs),
                "language": asdict(m.language),
                "length": asdict(m.length),
                "unsupported_claims": asdict(m.unsupported_claims),
            },
        )
        db.add(result)
    db.commit()
    db.refresh(run)
    return run
