from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Boolean,
    Text,
    DateTime,
    Float,
    JSON,
    ForeignKey,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

connect_args = {"check_same_thread": False} if DATABASE_URL and DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    leads = relationship("Lead", back_populates="campaign", cascade="all, delete-orphan")
    criteria_versions = relationship(
        "CampaignCriteria", back_populates="campaign", cascade="all, delete-orphan", order_by="CampaignCriteria.version"
    )


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    company = Column(String, nullable=False)
    industry = Column(String)
    country = Column(String)
    website = Column(String)
    email = Column(String)
    dedup_key = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="new")
    opened = Column(Boolean, default=False)
    message = Column(Text)  # legacy column from the pre-campaign schema; no longer written by new code
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    campaign = relationship("Campaign", back_populates="leads")
    draft = relationship("Draft", back_populates="lead", uselist=False, cascade="all, delete-orphan")
    research = relationship("CompanyResearch", back_populates="lead", uselist=False, cascade="all, delete-orphan")
    qualification = relationship("Qualification", back_populates="lead", uselist=False, cascade="all, delete-orphan")
    labels = relationship(
        "QualificationLabel", back_populates="lead", cascade="all, delete-orphan", order_by="QualificationLabel.labeled_at"
    )

    __table_args__ = (
        UniqueConstraint("campaign_id", "dedup_key", name="uq_lead_campaign_dedup"),
    )


class Draft(Base):
    __tablename__ = "drafts"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, unique=True, index=True)
    version = Column(Integer, nullable=False, default=1)  # current version number; content mirrors the latest DraftVersion
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    approval_status = Column(String, nullable=False, default="pending")  # pending | approved | rejected
    approved_version = Column(Integer)
    approved_subject = Column(String)
    approved_body = Column(Text)
    approved_recipient_email = Column(String)
    approved_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    lead = relationship("Lead", back_populates="draft")
    versions = relationship(
        "DraftVersion", back_populates="draft", cascade="all, delete-orphan", order_by="DraftVersion.version_number"
    )

    def latest_version(self):
        return max(self.versions, key=lambda v: v.version_number) if self.versions else None

    def is_approved_for_send(self) -> bool:
        """Bound to the exact version NUMBER, subject, body, and recipient at
        approval time -- any one of them changing (an edit, a regeneration,
        or the lead's email changing) invalidates approval."""
        recipient = self.lead.email if self.lead else None
        return (
            self.approval_status == "approved"
            and self.approved_version == self.version
            and bool(recipient)
            and self.approved_recipient_email == recipient
            and self.approved_subject == self.subject
            and self.approved_body == self.body
        )


class DraftVersion(Base):
    """Immutable history: every (re)generation and every manual edit adds a
    new row here rather than overwriting the previous one. Draft.subject/
    Draft.body mirror the latest row's content for the existing call sites
    (approval, sending) that read them directly."""

    __tablename__ = "draft_versions"

    id = Column(Integer, primary_key=True, index=True)
    draft_id = Column(Integer, ForeignKey("drafts.id"), nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    language = Column(String, nullable=False, default="en")
    tone = Column(String, nullable=False, default="professional")
    length = Column(String, nullable=False, default="medium")
    claim_sources = Column(JSON, nullable=False, default=list)
    review_flags = Column(JSON, nullable=False, default=list)
    is_generic_fallback = Column(Boolean, nullable=False, default=False)
    research_version_snapshot = Column(Integer, nullable=False, default=0)
    criteria_version_snapshot = Column(Integer, nullable=True)
    prompt_version = Column(String, nullable=False)
    model = Column(String, nullable=False)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    edited_manually = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    draft = relationship("Draft", back_populates="versions")

    __table_args__ = (UniqueConstraint("draft_id", "version_number", name="uq_draft_version_number"),)


class SendAttempt(Base):
    __tablename__ = "send_attempts"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    draft_version = Column(Integer, nullable=False)
    recipient_email = Column(String, nullable=False)
    dry_run = Column(Boolean, nullable=False, default=True)
    success = Column(Boolean, nullable=False)
    error = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CompanyResearch(Base):
    __tablename__ = "company_research"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, unique=True, index=True)
    website = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")  # pending|in_progress|completed|failed|needs_website
    version = Column(Integer, nullable=False, default=0)  # increments only when facts actually change (successful run)
    error = Column(String)
    pages_fetched = Column(Integer, default=0)
    offerings_unknown = Column(Boolean, nullable=False, default=True)
    target_customers_unknown = Column(Boolean, nullable=False, default=True)
    has_conflicts = Column(Boolean, nullable=False, default=False)
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    expires_at = Column(DateTime(timezone=True))
    last_refresh_error = Column(String)
    last_refresh_attempted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    lead = relationship("Lead", back_populates="research")
    facts = relationship("ResearchFact", back_populates="research", cascade="all, delete-orphan")


class ResearchFact(Base):
    __tablename__ = "research_facts"

    id = Column(Integer, primary_key=True, index=True)
    research_id = Column(Integer, ForeignKey("company_research.id"), nullable=False, index=True)
    category = Column(String, nullable=False)  # offering | target_customer | fact
    key = Column(String)  # only set for category == "fact"
    text = Column(Text, nullable=False)
    excerpt = Column(Text, nullable=False)
    source_url = Column(String, nullable=False)
    retrieved_at = Column(DateTime(timezone=True), nullable=False)
    conflicting = Column(Boolean, nullable=False, default=False)

    research = relationship("CompanyResearch", back_populates="facts")


class CampaignCriteria(Base):
    """Immutable, append-only per-campaign qualification criteria. Editing
    criteria in the UI creates a NEW row with version = previous max + 1; nothing
    is ever mutated in place, so past Qualification results can always be
    compared against the exact criteria they were scored against."""

    __tablename__ = "campaign_criteria"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    product_service = Column(Text)
    target_industries = Column(JSON, nullable=False, default=list)
    target_countries = Column(JSON, nullable=False, default=list)
    preferred_company_size = Column(String)
    business_needs = Column(JSON, nullable=False, default=list)
    exclusion_criteria = Column(JSON, nullable=False, default=list)
    weights = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    campaign = relationship("Campaign", back_populates="criteria_versions")

    __table_args__ = (UniqueConstraint("campaign_id", "version", name="uq_campaign_criteria_version"),)


class Qualification(Base):
    """The current deterministic qualification result for a lead. Overwritten
    in place each time qualification is (re)computed -- like Draft, this holds
    only the latest result; staleness relative to the campaign's current
    criteria version / the lead's current research version is computed at
    read time (see qualification.is_stale), never stored as a flag that could
    drift out of sync."""

    __tablename__ = "qualifications"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, unique=True, index=True)
    criteria_id = Column(Integer, ForeignKey("campaign_criteria.id"), nullable=False)
    criteria_version = Column(Integer, nullable=False)
    research_version_snapshot = Column(Integer, nullable=False)
    rubric_version = Column(String, nullable=False)
    fit_score = Column(Float)  # 0-100, or null if evidence_coverage == 0 (undefined)
    evidence_coverage = Column(Float, nullable=False)  # 0-1: fraction of configured criteria with known evidence
    matched = Column(JSON, nullable=False, default=list)
    unmatched = Column(JSON, nullable=False, default=list)
    unknown = Column(JSON, nullable=False, default=list)
    exclusions = Column(JSON, nullable=False, default=list)
    computed_at = Column(DateTime(timezone=True), server_default=func.now())

    lead = relationship("Lead", back_populates="qualification")


class QualificationLabel(Base):
    """Append-only manual review labels. Kept fully separate from the
    automated Qualification score/table -- a label is a human judgment, never
    derived from or overwritten by the deterministic scorer."""

    __tablename__ = "qualification_labels"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    label = Column(String, nullable=False)  # suitable | unsuitable | unsure
    notes = Column(Text)
    criteria_version_reviewed = Column(Integer, nullable=False)
    research_version_reviewed = Column(Integer, nullable=False)
    labeled_at = Column(DateTime(timezone=True), server_default=func.now())

    lead = relationship("Lead", back_populates="labels")


class Job(Base):
    """A background unit of work dispatched to Celery. Rows are committed to
    the database FIRST (status='queued', dispatched=False); a separate
    dispatch step then publishes to the broker and flips `dispatched` --  see
    job_service.dispatch_job / sweep_undispatched_jobs for the durable-outbox
    mechanism this supports. PostgreSQL (this table) is the authoritative
    status store; Celery/Redis are only the delivery mechanism."""

    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False, index=True)
    job_type = Column(String, nullable=False)  # research | qualify | draft | workflow
    status = Column(String, nullable=False, default="queued", index=True)
    # queued | running | succeeded | failed | cancelled | needs_review
    params = Column(JSON, nullable=False, default=dict)
    dispatched = Column(Boolean, nullable=False, default=False)
    dispatch_attempts = Column(Integer, nullable=False, default=0)
    celery_task_id = Column(String, nullable=True)
    idempotency_key = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))

    campaign = relationship("Campaign")
    steps = relationship("JobStep", back_populates="job", cascade="all, delete-orphan", order_by="JobStep.id")


class JobStep(Base):
    """One (lead, step_type) unit of work within a Job. The unique constraint
    on (job_id, lead_id, step_type) is what makes duplicate task delivery
    safe: a second delivery's atomic claim (see job_service.claim_step)
    simply affects zero rows instead of redoing/reduplicating work."""

    __tablename__ = "job_steps"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    step_type = Column(String, nullable=False)  # research | qualify | draft
    status = Column(String, nullable=False, default="queued", index=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    lease_owner = Column(String, nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    input_version_snapshot = Column(JSON, nullable=False, default=dict)
    result_summary = Column(JSON, nullable=True)
    last_error = Column(String, nullable=True)
    celery_task_id = Column(String, nullable=True)
    idempotency_key = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))

    job = relationship("Job", back_populates="steps")
    lead = relationship("Lead")

    __table_args__ = (UniqueConstraint("job_id", "lead_id", "step_type", name="uq_job_step_lead_type"),)


class EvalRun(Base):
    """One run of the evaluation harness (app/evaluation/) over a synthetic
    dataset version. Entirely separate from Lead/QualificationLabel -- eval
    cases are never real leads and eval runs are never presented as ML
    training data."""

    __tablename__ = "eval_runs"

    id = Column(Integer, primary_key=True, index=True)
    dataset_version = Column(String, nullable=False)
    mode = Column(String, nullable=False)  # mock | live -- see evaluation/harness.py
    model_basic = Column(String, nullable=False)
    model_evidence_based = Column(String, nullable=False)
    prompt_version_basic = Column(String, nullable=False)
    prompt_version_evidence_based = Column(String, nullable=False)
    language = Column(String, nullable=False)
    tone = Column(String, nullable=False)
    length = Column(String, nullable=False)
    max_calls = Column(Integer, nullable=True)  # the budget passed in; null for mock runs (no real calls made)
    calls_made = Column(Integer, nullable=False, default=0)
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    results = relationship("EvalResult", back_populates="run", cascade="all, delete-orphan", order_by="EvalResult.id")


class EvalResult(Base):
    """One (case, approach) generation result within an EvalRun, plus its
    deterministic metrics and any human review. `human_rating_*`/`human_notes`
    are null until a person actually rates it via the evaluation UI -- never
    auto-filled, and never confused with QualificationLabel (which labels
    real leads for the separate ML qualification model)."""

    __tablename__ = "eval_results"

    id = Column(Integer, primary_key=True, index=True)
    eval_run_id = Column(Integer, ForeignKey("eval_runs.id"), nullable=False, index=True)
    case_id = Column(String, nullable=False, index=True)
    case_tags = Column(JSON, nullable=False, default=list)
    approach = Column(String, nullable=False)  # basic | evidence_based
    subject = Column(Text)
    body = Column(Text)
    raw_output = Column(JSON)
    error = Column(Text)  # set instead of subject/body if generation itself raised
    latency_ms = Column(Float, nullable=True)  # only set when actually measured (live mode)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    metrics = Column(JSON, nullable=False, default=dict)  # deterministic checks, see evaluation/metrics.py
    model_judge = Column(JSON, nullable=True)  # optional LLM-judge assessment; null unless explicitly run
    human_rating_relevance = Column(Integer, nullable=True)  # 1-5; null = not evaluated
    human_rating_personalization = Column(Integer, nullable=True)  # 1-5; null = not evaluated
    human_notes = Column(Text, nullable=True)
    human_rated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    run = relationship("EvalRun", back_populates="results")

    __table_args__ = (UniqueConstraint("eval_run_id", "case_id", "approach", name="uq_eval_result_run_case_approach"),)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
