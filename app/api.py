from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from database import init_db, get_db, Campaign, Lead, EvalRun, EvalResult
from schemas import (
    CampaignCreate,
    DraftGenerateRequest,
    DraftEditRequest,
    SendRequest,
    ResearchRequest,
    CriteriaRequest,
    LabelRequest,
    JobCreateRequest,
    EvalRatingRequest,
)
import services
import research_service
import qualification_service
import draft_service
import ml_experiment
import job_service
import tasks

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()


def _service_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (
        services.ServiceError,
        research_service.ResearchServiceError,
        qualification_service.QualificationServiceError,
        draft_service.DraftServiceError,
        job_service.JobServiceError,
    ) as e:
        raise HTTPException(status_code=400, detail=str(e))


def _lead_out(lead: Lead, db: Session = None) -> dict:
    draft = lead.draft
    qualification = lead.qualification
    current_label = lead.labels[-1] if lead.labels else None
    return {
        "id": lead.id,
        "campaign_id": lead.campaign_id,
        "name": lead.name,
        "company": lead.company,
        "industry": lead.industry,
        "country": lead.country,
        "website": lead.website,
        "email": lead.email,
        "status": lead.status,
        "opened": lead.opened,
        "draft": _draft_out(draft) if draft else None,
        "research_status": lead.research.status if lead.research else None,
        "fit_score": qualification.fit_score if qualification else None,
        "evidence_coverage": qualification.evidence_coverage if qualification else None,
        "qualification_stale": qualification_service.is_stale(db, qualification) if (qualification and db is not None) else None,
        "current_label": current_label.label if current_label else None,
    }


def _draft_version_out(version) -> dict:
    return {
        "id": version.id,
        "version_number": version.version_number,
        "subject": version.subject,
        "body": version.body,
        "language": version.language,
        "tone": version.tone,
        "length": version.length,
        "claim_sources": version.claim_sources,
        "review_flags": version.review_flags,
        "is_generic_fallback": version.is_generic_fallback,
        "research_version_snapshot": version.research_version_snapshot,
        "criteria_version_snapshot": version.criteria_version_snapshot,
        "prompt_version": version.prompt_version,
        "model": version.model,
        "prompt_tokens": version.prompt_tokens,
        "completion_tokens": version.completion_tokens,
        "total_tokens": version.total_tokens,
        "edited_manually": version.edited_manually,
        "created_at": version.created_at,
    }


def _draft_out(draft) -> dict:
    latest = draft.latest_version()
    return {
        "id": draft.id,
        "lead_id": draft.lead_id,
        "version": draft.version,
        "subject": draft.subject,
        "body": draft.body,
        "approval_status": draft.approval_status,
        "is_approved_for_send": draft.is_approved_for_send(),
        "approved_version": draft.approved_version,
        "approved_recipient_email": draft.approved_recipient_email,
        "claim_sources": latest.claim_sources if latest else [],
        "review_flags": latest.review_flags if latest else [],
        "is_generic_fallback": latest.is_generic_fallback if latest else False,
        "language": latest.language if latest else None,
        "tone": latest.tone if latest else None,
        "length": latest.length if latest else None,
        "research_version_snapshot": latest.research_version_snapshot if latest else None,
        "criteria_version_snapshot": latest.criteria_version_snapshot if latest else None,
        "prompt_version": latest.prompt_version if latest else None,
        "model": latest.model if latest else None,
    }


def _fact_out(fact) -> dict:
    return {
        "id": fact.id,
        "category": fact.category,
        "key": fact.key,
        "text": fact.text,
        "excerpt": fact.excerpt,
        "source_url": fact.source_url,
        "retrieved_at": fact.retrieved_at,
        "conflicting": fact.conflicting,
    }


def _research_out(research) -> dict:
    facts = list(research.facts) if research.facts is not None else []
    return {
        "lead_id": research.lead_id,
        "website": research.website,
        "status": research.status,
        "error": research.error,
        "pages_fetched": research.pages_fetched or 0,
        "offerings": [_fact_out(f) for f in facts if f.category == "offering"],
        "target_customers": [_fact_out(f) for f in facts if f.category == "target_customer"],
        "other_facts": [_fact_out(f) for f in facts if f.category == "fact"],
        "offerings_unknown": research.offerings_unknown,
        "target_customers_unknown": research.target_customers_unknown,
        "has_conflicts": research.has_conflicts,
        "completed_at": research.completed_at,
        "expires_at": research.expires_at,
        "last_refresh_error": research.last_refresh_error,
        "last_refresh_attempted_at": research.last_refresh_attempted_at,
    }


def _criteria_out(criteria) -> dict:
    return {
        "id": criteria.id,
        "campaign_id": criteria.campaign_id,
        "version": criteria.version,
        "product_service": criteria.product_service,
        "target_industries": criteria.target_industries,
        "target_countries": criteria.target_countries,
        "preferred_company_size": criteria.preferred_company_size,
        "business_needs": criteria.business_needs,
        "exclusion_criteria": criteria.exclusion_criteria,
        "weights": criteria.weights,
        "created_at": criteria.created_at,
    }


def _qualification_out(qualification, db: Session) -> dict:
    return {
        "lead_id": qualification.lead_id,
        "criteria_version": qualification.criteria_version,
        "research_version_snapshot": qualification.research_version_snapshot,
        "rubric_version": qualification.rubric_version,
        "fit_score": qualification.fit_score,
        "evidence_coverage": qualification.evidence_coverage,
        "matched": qualification.matched,
        "unmatched": qualification.unmatched,
        "unknown": qualification.unknown,
        "exclusions": qualification.exclusions,
        "is_stale": qualification_service.is_stale(db, qualification),
        "computed_at": qualification.computed_at,
    }


def _label_out(label) -> dict:
    return {
        "id": label.id,
        "lead_id": label.lead_id,
        "label": label.label,
        "notes": label.notes,
        "criteria_version_reviewed": label.criteria_version_reviewed,
        "research_version_reviewed": label.research_version_reviewed,
        "labeled_at": label.labeled_at,
    }


@app.post("/campaigns")
def create_campaign(payload: CampaignCreate, db: Session = Depends(get_db)):
    campaign = _service_call(services.create_campaign, db, payload.name)
    return {"id": campaign.id, "name": campaign.name}


@app.get("/campaigns")
def list_campaigns(db: Session = Depends(get_db)):
    campaigns = db.query(Campaign).all()
    return [
        {"id": c.id, "name": c.name, "lead_count": len(c.leads)}
        for c in campaigns
    ]


@app.get("/campaigns/{campaign_id}/leads")
def list_campaign_leads(campaign_id: int, db: Session = Depends(get_db)):
    _service_call(services.get_campaign_or_404, db, campaign_id)
    leads = db.query(Lead).filter(Lead.campaign_id == campaign_id).all()
    return [_lead_out(l, db) for l in leads]


@app.post("/campaigns/{campaign_id}/leads/import")
async def import_leads(campaign_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    contents = await file.read()
    return _service_call(services.import_leads, db, campaign_id, contents)


@app.post("/campaigns/{campaign_id}/drafts/generate")
def generate_drafts(campaign_id: int, payload: DraftGenerateRequest = DraftGenerateRequest(), db: Session = Depends(get_db)):
    drafts = _service_call(
        draft_service.generate_drafts_for_campaign,
        db,
        campaign_id,
        payload.lead_ids,
        language=payload.language,
        tone=payload.tone,
        length=payload.length,
    )
    return [_draft_out(d) for d in drafts]


@app.get("/campaigns/{campaign_id}/drafts")
def list_drafts(campaign_id: int, db: Session = Depends(get_db)):
    _service_call(services.get_campaign_or_404, db, campaign_id)
    leads = db.query(Lead).filter(Lead.campaign_id == campaign_id).all()
    return [_draft_out(l.draft) for l in leads if l.draft is not None]


@app.patch("/drafts/{draft_id}")
def edit_draft(draft_id: int, payload: DraftEditRequest, db: Session = Depends(get_db)):
    draft = _service_call(draft_service.edit_draft, db, draft_id, payload.subject, payload.body)
    return _draft_out(draft)


@app.post("/drafts/{draft_id}/approve")
def approve_draft(draft_id: int, db: Session = Depends(get_db)):
    draft = _service_call(draft_service.approve_draft, db, draft_id)
    return _draft_out(draft)


@app.post("/drafts/{draft_id}/reject")
def reject_draft(draft_id: int, db: Session = Depends(get_db)):
    draft = _service_call(draft_service.reject_draft, db, draft_id)
    return _draft_out(draft)


@app.get("/drafts/{draft_id}/history")
def get_draft_history(draft_id: int, db: Session = Depends(get_db)):
    versions = _service_call(draft_service.get_draft_history, db, draft_id)
    return [_draft_version_out(v) for v in versions]


@app.post("/campaigns/{campaign_id}/send")
def send_campaign(campaign_id: int, payload: SendRequest = SendRequest(), db: Session = Depends(get_db)):
    return _service_call(services.send_campaign, db, campaign_id, payload.dry_run, payload.lead_ids)


@app.get("/stats")
def get_stats(campaign_id: int | None = Query(default=None), db: Session = Depends(get_db)):
    return services.get_stats(db, campaign_id)


@app.post("/run")
def run_agent(campaign_id: int, db: Session = Depends(get_db)):
    """Deprecated: generates drafts for a campaign. No longer deletes leads or sends
    email — use POST /campaigns/{id}/drafts/generate and POST /campaigns/{id}/send instead."""
    drafts = _service_call(draft_service.generate_drafts_for_campaign, db, campaign_id, None)
    return {"message": f"Generated {len(drafts)} draft(s) for campaign {campaign_id}.", "drafts": [_draft_out(d) for d in drafts]}


@app.get("/leads/{lead_id}/research")
def get_lead_research(lead_id: int, db: Session = Depends(get_db)):
    research = _service_call(research_service.get_research, db, lead_id)
    return _research_out(research)


@app.post("/leads/{lead_id}/research")
def run_lead_research(lead_id: int, payload: ResearchRequest = ResearchRequest(), db: Session = Depends(get_db)):
    research = _service_call(
        research_service.run_research,
        db,
        lead_id,
        force_refresh=payload.force_refresh,
        page_budget=payload.page_budget,
    )
    return _research_out(research)


@app.post("/campaigns/{campaign_id}/criteria")
def create_criteria(campaign_id: int, payload: CriteriaRequest, db: Session = Depends(get_db)):
    criteria = _service_call(
        qualification_service.create_criteria,
        db,
        campaign_id,
        product_service=payload.product_service,
        target_industries=payload.target_industries,
        target_countries=payload.target_countries,
        preferred_company_size=payload.preferred_company_size,
        business_needs=payload.business_needs,
        exclusion_criteria=payload.exclusion_criteria,
        weights=payload.weights,
    )
    return _criteria_out(criteria)


@app.get("/campaigns/{campaign_id}/criteria")
def list_criteria(campaign_id: int, db: Session = Depends(get_db)):
    _service_call(services.get_campaign_or_404, db, campaign_id)
    campaign = db.get(Campaign, campaign_id)
    return [_criteria_out(c) for c in campaign.criteria_versions]


@app.post("/leads/{lead_id}/qualify")
def qualify_lead(lead_id: int, db: Session = Depends(get_db)):
    qualification = _service_call(qualification_service.qualify_lead, db, lead_id)
    return _qualification_out(qualification, db)


@app.post("/campaigns/{campaign_id}/qualify")
def qualify_campaign(campaign_id: int, db: Session = Depends(get_db)):
    qualifications = _service_call(qualification_service.qualify_campaign, db, campaign_id)
    return [_qualification_out(q, db) for q in qualifications]


@app.get("/leads/{lead_id}/qualification")
def get_lead_qualification(lead_id: int, db: Session = Depends(get_db)):
    qualification = _service_call(qualification_service.get_qualification, db, lead_id)
    if qualification is None:
        return None
    return _qualification_out(qualification, db)


@app.get("/campaigns/{campaign_id}/qualifications")
def list_campaign_qualifications(campaign_id: int, db: Session = Depends(get_db)):
    _service_call(services.get_campaign_or_404, db, campaign_id)
    leads = db.query(Lead).filter(Lead.campaign_id == campaign_id).all()
    out = []
    for lead in leads:
        entry = _lead_out(lead, db)
        entry["qualification"] = _qualification_out(lead.qualification, db) if lead.qualification else None
        out.append(entry)
    return out


@app.post("/leads/{lead_id}/qualification/labels")
def add_qualification_label(lead_id: int, payload: LabelRequest, db: Session = Depends(get_db)):
    label = _service_call(qualification_service.add_label, db, lead_id, payload.label, payload.notes)
    return _label_out(label)


@app.get("/leads/{lead_id}/qualification/labels")
def list_qualification_labels(lead_id: int, db: Session = Depends(get_db)):
    labels = _service_call(qualification_service.get_labels, db, lead_id)
    return [_label_out(l) for l in labels]


def _job_step_out(step) -> dict:
    return {
        "id": step.id,
        "lead_id": step.lead_id,
        "step_type": step.step_type,
        "status": step.status,
        "attempt_count": step.attempt_count,
        "max_attempts": step.max_attempts,
        "last_error": step.last_error,
        "result_summary": step.result_summary,
        "started_at": step.started_at,
        "finished_at": step.finished_at,
    }


def _job_out(job) -> dict:
    return {
        "id": job.id,
        "campaign_id": job.campaign_id,
        "job_type": job.job_type,
        "status": job.status,
        "params": job.params,
        "dispatched": job.dispatched,
        "dispatch_attempts": job.dispatch_attempts,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "steps": [_job_step_out(s) for s in job.steps],
    }


def _create_and_dispatch_job(db: Session, campaign_id: int, job_type: str, payload: JobCreateRequest) -> dict:
    lead_ids = payload.lead_ids
    if not lead_ids:
        lead_ids = [l.id for l in db.query(Lead).filter(Lead.campaign_id == campaign_id).all()]

    params = {
        "language": payload.language,
        "tone": payload.tone,
        "length": payload.length,
        "force_refresh": payload.force_refresh,
        "page_budget": payload.page_budget,
    }
    job = _service_call(job_service.create_job, db, campaign_id, job_type, lead_ids, params)
    tasks.dispatch_new_job(db, job.id)
    db.refresh(job)
    return _job_out(job)


@app.post("/campaigns/{campaign_id}/jobs/workflow", status_code=202)
def create_workflow_job(campaign_id: int, payload: JobCreateRequest = JobCreateRequest(), db: Session = Depends(get_db)):
    """Runs research -> qualification -> draft for each lead in the
    background. Stops at draft review -- this never approves or sends."""
    return _create_and_dispatch_job(db, campaign_id, "workflow", payload)


@app.post("/campaigns/{campaign_id}/jobs/research", status_code=202)
def create_research_job(campaign_id: int, payload: JobCreateRequest = JobCreateRequest(), db: Session = Depends(get_db)):
    return _create_and_dispatch_job(db, campaign_id, "research", payload)


@app.post("/campaigns/{campaign_id}/jobs/qualify", status_code=202)
def create_qualify_job(campaign_id: int, payload: JobCreateRequest = JobCreateRequest(), db: Session = Depends(get_db)):
    return _create_and_dispatch_job(db, campaign_id, "qualify", payload)


@app.post("/campaigns/{campaign_id}/jobs/draft", status_code=202)
def create_draft_job(campaign_id: int, payload: JobCreateRequest = JobCreateRequest(), db: Session = Depends(get_db)):
    return _create_and_dispatch_job(db, campaign_id, "draft", payload)


@app.get("/campaigns/{campaign_id}/jobs")
def list_campaign_jobs(campaign_id: int, db: Session = Depends(get_db)):
    _service_call(services.get_campaign_or_404, db, campaign_id)
    jobs = job_service.list_jobs_for_campaign(db, campaign_id)
    return [_job_out(j) for j in jobs]


@app.get("/jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = _service_call(job_service.get_job_or_404, db, job_id)
    return _job_out(job)


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, db: Session = Depends(get_db)):
    job = _service_call(job_service.cancel_job, db, job_id)
    return _job_out(job)


@app.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, db: Session = Depends(get_db)):
    """Resumes THIS job's failed/needs_review/cancelled steps only --
    already-succeeded steps (and any DraftVersion they created) are left
    untouched. This is deliberately distinct from POST .../jobs/workflow
    (etc.), which always creates a brand-new job/intentional new generation."""
    _service_call(job_service.resume_job, db, job_id)
    ok = tasks.dispatch_new_job(db, job_id)
    job = job_service.get_job_or_404(db, job_id)
    return {**_job_out(job), "redispatched": ok}


@app.get("/ml/label-readiness")
def get_label_readiness(db: Session = Depends(get_db)):
    """Read-only report on whether enough human-reviewed labels exist yet to
    train the optional suitability-prediction model (see ml_experiment.py).
    Never triggers training itself."""
    assessment = ml_experiment.assess_label_readiness(db)
    return {
        "total_labeled_leads": assessment.total_labeled_leads,
        "class_counts": assessment.class_counts,
        "unique_companies": assessment.unique_companies,
        "unique_campaigns": assessment.unique_campaigns,
        "sufficient_for_training": assessment.sufficient_for_training,
        "reasons": assessment.reasons,
    }


@app.post("/research")
def run_research_endpoint(campaign_id: int, db: Session = Depends(get_db)):
    from modules.research import run_research

    _service_call(services.get_campaign_or_404, db, campaign_id)
    leads = db.query(Lead).filter(Lead.campaign_id == campaign_id).all()
    lead_dicts = [
        {"name": l.name, "company": l.company, "industry": l.industry, "country": l.country}
        for l in leads
    ]
    results = run_research(lead_dicts)
    return {"results": results}


# --- Evaluation (app/evaluation/) -------------------------------------------
# Read-only visibility into offline/live drafting-comparison runs, plus
# human rating submission. Never triggers a run itself (that's
# `python -m evaluation.run_eval`, run out-of-band, deliberately not
# reachable from the API so a stray request can never trigger a paid call).


@app.get("/evaluation/dataset")
def get_evaluation_dataset(dataset: str = "companies_v1"):
    from evaluation.dataset import load_dataset

    ds = load_dataset(dataset)
    return {
        "version": ds.version,
        "campaign_offering": ds.campaign_offering,
        "case_count": len(ds.cases),
        "cases": [
            {"id": c.id, "company": c.company, "tags": c.tags, "notes": c.notes, "synthetic": True}
            for c in ds.cases
        ],
    }


@app.get("/evaluation/runs")
def list_evaluation_runs(db: Session = Depends(get_db)):
    runs = db.query(EvalRun).order_by(EvalRun.id.desc()).all()
    return [
        {
            "id": r.id,
            "dataset_version": r.dataset_version,
            "mode": r.mode,
            "model_basic": r.model_basic,
            "model_evidence_based": r.model_evidence_based,
            "prompt_version_basic": r.prompt_version_basic,
            "prompt_version_evidence_based": r.prompt_version_evidence_based,
            "language": r.language,
            "tone": r.tone,
            "length": r.length,
            "max_calls": r.max_calls,
            "calls_made": r.calls_made,
            "notes": r.notes,
            "created_at": r.created_at,
            "result_count": len(r.results),
        }
        for r in runs
    ]


def _eval_result_out(r: EvalResult) -> dict:
    return {
        "id": r.id,
        "case_id": r.case_id,
        "case_tags": r.case_tags,
        "approach": r.approach,
        "subject": r.subject,
        "body": r.body,
        "error": r.error,
        "latency_ms": r.latency_ms,
        "prompt_tokens": r.prompt_tokens,
        "completion_tokens": r.completion_tokens,
        "total_tokens": r.total_tokens,
        "metrics": r.metrics,
        "model_judge": r.model_judge,
        "human_rating_relevance": r.human_rating_relevance,
        "human_rating_personalization": r.human_rating_personalization,
        "human_notes": r.human_notes,
        "human_rated_at": r.human_rated_at,
        "evaluated": r.human_rated_at is not None,
    }


@app.get("/evaluation/runs/{run_id}/results")
def get_evaluation_run_results(run_id: int, db: Session = Depends(get_db)):
    run = db.get(EvalRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Evaluation run {run_id} not found.")
    results = db.query(EvalResult).filter(EvalResult.eval_run_id == run_id).order_by(EvalResult.case_id, EvalResult.approach).all()
    return {
        "run": {
            "id": run.id,
            "dataset_version": run.dataset_version,
            "mode": run.mode,
            "language": run.language,
            "tone": run.tone,
            "length": run.length,
            "max_calls": run.max_calls,
            "calls_made": run.calls_made,
            "created_at": run.created_at,
        },
        "results": [_eval_result_out(r) for r in results],
    }


@app.post("/evaluation/results/{result_id}/rating")
def submit_evaluation_rating(result_id: int, payload: EvalRatingRequest, db: Session = Depends(get_db)):
    from datetime import datetime, timezone

    result = db.get(EvalResult, result_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Evaluation result {result_id} not found.")
    if payload.relevance is not None:
        result.human_rating_relevance = payload.relevance
    if payload.personalization is not None:
        result.human_rating_personalization = payload.personalization
    if payload.notes is not None:
        result.human_notes = payload.notes
    result.human_rated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(result)
    return _eval_result_out(result)
