"""Evidence-based, versioned draft generation, editing, and approval.

Generation inputs (requirement #1): the campaign's current criteria (for its
product/service description and exclusion list), the lead's validated
ResearchFact rows, and the lead's current Qualification. Missing, expired, or
conflicting evidence is never silently used -- see `_build_evidence_context`,
which always returns a `review_flags` list describing exactly what evidence
was and wasn't available, persisted alongside the generated content.

Every (re)generation and every manual edit creates a NEW, immutable
DraftVersion row (requirement #4) -- prior versions are never overwritten.
Draft.subject/Draft.body/Draft.version continue to mirror the latest version,
so the pre-existing approve/send code path (Draft.is_approved_for_send,
services.send_campaign) keeps working unchanged. Approval is bound to the
exact version number, subject, body, AND recipient email (see
Draft.is_approved_for_send on the model) -- editing or regenerating always
resets approval to pending.

Generation never calls email_sender and never touches SendAttempt -- sending
is only ever triggered by services.send_campaign, kept entirely separate.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import Lead, Draft, DraftVersion
from modules import draft_generator, draft_safety
import qualification_service
from services import get_campaign_or_404

PROMPT_VERSION = "v1"

VALID_LANGUAGES = {"en", "mk"}
VALID_TONES = {"professional", "friendly", "formal", "casual"}
VALID_LENGTHS = {"short", "medium", "long"}

DEFAULT_LANGUAGE = "en"
DEFAULT_TONE = "professional"
DEFAULT_LENGTH = "medium"


class DraftServiceError(Exception):
    """Raised for request-level errors that should map to a 4xx HTTP response."""


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class EvidenceContext:
    def __init__(self, product_service, criteria_version, facts, research_version, review_flags, is_generic_fallback):
        self.product_service = product_service
        self.criteria_version = criteria_version
        self.facts = facts
        self.research_version = research_version
        self.review_flags = review_flags
        self.is_generic_fallback = is_generic_fallback


def _build_evidence_context(db: Session, lead: Lead) -> EvidenceContext:
    review_flags = []

    criteria = qualification_service.get_current_criteria(db, lead.campaign_id)
    if not criteria:
        raise DraftServiceError(
            "This campaign has no criteria configured yet -- a product/service description is required before generating drafts."
        )

    research = lead.research
    facts = []
    research_version = 0

    if research is None or research.status != "completed":
        review_flags.append(
            {"type": "missing_research", "detail": "No completed company research is available for this lead."}
        )
    else:
        research_version = research.version
        facts = list(research.facts)

        expires_at = _aware(research.expires_at)
        if expires_at is not None and expires_at < datetime.now(timezone.utc):
            review_flags.append(
                {
                    "type": "expired_research",
                    "detail": f"Research cache expired at {expires_at.isoformat()}; consider refreshing before relying on it.",
                }
            )

        if research.has_conflicts:
            conflicting_count = sum(1 for f in facts if f.conflicting)
            review_flags.append(
                {
                    "type": "conflicting_research_evidence",
                    "detail": f"{conflicting_count} research fact(s) conflict across sources; they were excluded from generation.",
                }
            )
            facts = [f for f in facts if not f.conflicting]  # never let the generator cite a conflicting fact

    qualification = lead.qualification
    if qualification is None:
        review_flags.append(
            {"type": "missing_qualification", "detail": "This lead has not been qualified against campaign criteria yet."}
        )
    else:
        if qualification_service.is_stale(db, qualification):
            review_flags.append(
                {
                    "type": "stale_qualification",
                    "detail": "Qualification is stale relative to current criteria/research; re-qualify before relying on it.",
                }
            )
        if qualification.exclusions:
            review_flags.append(
                {
                    "type": "exclusion_triggered",
                    "detail": f"{len(qualification.exclusions)} exclusion criterion/criteria matched for this lead -- review before sending.",
                    "exclusions": qualification.exclusions,
                }
            )

    return EvidenceContext(
        product_service=criteria.product_service or "",
        criteria_version=criteria.version,
        facts=facts,
        research_version=research_version,
        review_flags=review_flags,
        is_generic_fallback=(len(facts) == 0),
    )


def _validate_generation_params(language, tone, length):
    if language not in VALID_LANGUAGES:
        raise DraftServiceError(f"language must be one of {sorted(VALID_LANGUAGES)}, got '{language}'.")
    if tone not in VALID_TONES:
        raise DraftServiceError(f"tone must be one of {sorted(VALID_TONES)}, got '{tone}'.")
    if length not in VALID_LENGTHS:
        raise DraftServiceError(f"length must be one of {sorted(VALID_LENGTHS)}, got '{length}'.")


def _reset_approval(draft: Draft):
    draft.approval_status = "pending"
    draft.approved_version = None
    draft.approved_subject = None
    draft.approved_body = None
    draft.approved_recipient_email = None
    draft.approved_at = None


def _try_bump_draft_atomically(db: Session, lead_id: int, expected_base_version: int, **version_kwargs) -> Optional[Draft]:
    """Atomically advances the draft for `lead_id` from EXACTLY
    `expected_base_version` to `expected_base_version + 1` (or creates it, if
    `expected_base_version == 0` and none exists yet).

    This is a single UPDATE...WHERE version=X compare-and-swap (the same
    pattern job_service.claim_step uses) -- there is no separate "check the
    version, then write" step for a concurrent writer to slip in between.
    Two callers racing on the SAME lead's draft can both read
    `expected_base_version = N`, but only one of their UPDATEs will actually
    match `version = N` by the time it executes; the other's `rowcount` comes
    back 0 and it gets None back here, never a partially-applied write and
    never a silent overwrite of whichever one committed first.

    Returns the updated Draft, or None if a concurrent writer already moved
    the draft past `expected_base_version` (this includes the loser of a
    race to CREATE the first version, guarded by Draft.lead_id's own unique
    constraint).
    """
    existing = db.query(Draft).filter(Draft.lead_id == lead_id).first()

    if existing is None:
        if expected_base_version != 0:
            return None  # caller's snapshot ("no draft yet") is already stale
        draft = Draft(lead_id=lead_id, version=1, subject=version_kwargs["subject"], body=version_kwargs["body"], approval_status="pending")
        db.add(draft)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            return None  # a concurrent writer created the draft first
        db.add(DraftVersion(draft_id=draft.id, version_number=1, **version_kwargs))
        (db.get(Lead, lead_id)).status = "drafted"
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return None
        db.refresh(draft)
        return draft

    next_version = expected_base_version + 1
    stmt = (
        update(Draft)
        .where(Draft.id == existing.id, Draft.version == expected_base_version)
        .values(
            version=next_version,
            subject=version_kwargs["subject"],
            body=version_kwargs["body"],
            approval_status="pending",
            approved_version=None,
            approved_subject=None,
            approved_body=None,
            approved_recipient_email=None,
            approved_at=None,
        )
    )
    result = db.execute(stmt)
    if result.rowcount != 1:
        db.rollback()
        return None  # lost the race: the draft moved on between our read and this write

    db.add(DraftVersion(draft_id=existing.id, version_number=next_version, **version_kwargs))
    (db.get(Lead, lead_id)).status = "drafted"
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None  # extremely unlikely (draft_id, version_number) collision -- treat as lost race

    db.refresh(existing)
    return existing


def _persist_new_version(db: Session, lead: Lead, **version_kwargs) -> Draft:
    """Convenience wrapper for callers that don't need to detect a
    superseded write themselves (they just want "append a new version on
    top of whatever is there right now") -- reads the current version fresh
    and retries the atomic bump once if a genuine concurrent write is lost,
    since these callers represent a live, synchronous, current user
    intent (an edit or an explicit regenerate), not a stale background
    snapshot, and should not be silently dropped by an ordinary race."""
    for _ in range(2):
        db.expire(lead, ["draft"])
        expected_base_version = lead.draft.version if lead.draft else 0
        draft = _try_bump_draft_atomically(db, lead.id, expected_base_version, **version_kwargs)
        if draft is not None:
            return draft
    raise DraftServiceError("Could not save this draft due to a concurrent update; please reload and try again.")


def _generate_and_verify_content(lead: Lead, ctx: EvidenceContext, language: str, tone: str, length: str, call_model=None) -> dict:
    """The network/LLM call and all post-generation verification -- pure
    with respect to the database (reads only `lead`/`ctx`, already loaded;
    writes nothing). Kept separate from persistence so a caller can decide
    NOT to persist the result (see generate_draft_if_still_applicable)."""
    facts_for_prompt = [{"id": f.id, "category": f.category, "key": f.key, "text": f.text} for f in ctx.facts]

    try:
        generated = draft_generator.generate_draft(
            company=lead.company,
            contact_name=lead.name,
            product_service=ctx.product_service,
            facts=facts_for_prompt,
            language=language,
            tone=tone,
            length=length,
            call_model=call_model,
        )
    except draft_generator.DraftGenerationError as e:
        raise DraftServiceError(str(e))

    verified_claims, claim_flags = draft_safety.verify_claims(generated.claims, ctx.facts)
    inference_flags = draft_safety.scan_for_unsupported_inferences(generated.body, ctx.facts)

    facts_by_id = {f.id: f for f in ctx.facts}
    claim_sources = [
        {
            "claim": c.claim,
            "fact_id": c.fact_id,
            "source_url": facts_by_id[c.fact_id].source_url,
            "excerpt": facts_by_id[c.fact_id].excerpt,
        }
        for c in verified_claims
    ]

    review_flags = list(ctx.review_flags) + claim_flags + inference_flags
    if ctx.is_generic_fallback:
        review_flags.append(
            {
                "type": "generic_draft",
                "detail": "Insufficient validated research evidence; this is a generic, non-personalized draft.",
            }
        )

    return {
        "subject": generated.subject,
        "body": generated.body,
        "language": language,
        "tone": tone,
        "length": length,
        "claim_sources": claim_sources,
        "review_flags": review_flags,
        "is_generic_fallback": ctx.is_generic_fallback,
        "research_version_snapshot": ctx.research_version,
        "criteria_version_snapshot": ctx.criteria_version,
        "prompt_version": PROMPT_VERSION,
        "model": generated.model,
        "prompt_tokens": generated.usage.get("prompt_tokens"),
        "completion_tokens": generated.usage.get("completion_tokens"),
        "total_tokens": generated.usage.get("total_tokens"),
        "edited_manually": False,
    }


def generate_draft(
    db: Session,
    lead_id: int,
    *,
    language: str = DEFAULT_LANGUAGE,
    tone: str = DEFAULT_TONE,
    length: str = DEFAULT_LENGTH,
    call_model=None,
) -> Draft:
    _validate_generation_params(language, tone, length)

    lead = db.get(Lead, lead_id)
    if not lead:
        raise DraftServiceError(f"Lead {lead_id} not found.")

    ctx = _build_evidence_context(db, lead)
    content = _generate_and_verify_content(lead, ctx, language, tone, length, call_model=call_model)
    return _persist_new_version(db, lead, **content)


def current_draft_version(db: Session, lead_id: int) -> int:
    """The version number a background job should compare against once it
    finishes generating -- capture this at the START of a background step so
    a manual edit/regeneration that lands while the job is still running can
    be detected before the result is saved."""
    lead = db.get(Lead, lead_id)
    if not lead:
        raise DraftServiceError(f"Lead {lead_id} not found.")
    return lead.draft.version if lead.draft else 0


def generate_draft_if_still_applicable(
    db: Session,
    lead_id: int,
    expected_base_version: int,
    *,
    language: str = DEFAULT_LANGUAGE,
    tone: str = DEFAULT_TONE,
    length: str = DEFAULT_LENGTH,
    call_model=None,
) -> dict:
    """Background-safe generation: performs the SAME generation and
    verification as generate_draft, but re-checks immediately before writing
    whether the draft is still at `expected_base_version` (captured by the
    caller via current_draft_version() at the time the background step
    started). If someone edited or regenerated the draft in the meantime,
    the draft's version has already moved past that snapshot -- persisting
    this result as "the next version" would silently clobber that newer
    edit and could unexpectedly invalidate an approval made on it. In that
    case nothing is written; the generated content is returned so the caller
    (a Celery task) can store it for the user to inspect and explicitly
    choose to (re)run instead of it being silently discarded OR silently applied.

    Returns {"applied": True, "draft": Draft} or
            {"applied": False, "content": {...}} (superseded).
    """
    _validate_generation_params(language, tone, length)

    lead = db.get(Lead, lead_id)
    if not lead:
        raise DraftServiceError(f"Lead {lead_id} not found.")

    ctx = _build_evidence_context(db, lead)
    content = _generate_and_verify_content(lead, ctx, language, tone, length, call_model=call_model)

    # Atomic compare-and-swap write -- this IS the version-safety checkpoint
    # the requirement calls for ("before saving a worker result, check that
    # its inputs are still applicable"), implemented as a single UPDATE...
    # WHERE version=expected_base_version rather than a separate read-then-
    # write with a race window a concurrent editor could land in between.
    draft = _try_bump_draft_atomically(db, lead_id, expected_base_version, **content)
    if draft is None:
        db.expire(lead, ["draft"])
        current_version = lead.draft.version if lead.draft else 0
        return {"applied": False, "content": content, "current_version": current_version}

    return {"applied": True, "draft": draft}


def generate_drafts_for_campaign(
    db: Session,
    campaign_id: int,
    lead_ids: Optional[list] = None,
    *,
    language: str = DEFAULT_LANGUAGE,
    tone: str = DEFAULT_TONE,
    length: str = DEFAULT_LENGTH,
    call_model=None,
) -> list[Draft]:
    get_campaign_or_404(db, campaign_id)
    query = db.query(Lead).filter(Lead.campaign_id == campaign_id)
    if lead_ids:
        query = query.filter(Lead.id.in_(lead_ids))
    leads = query.all()
    return [generate_draft(db, lead.id, language=language, tone=tone, length=length, call_model=call_model) for lead in leads]


def get_draft_or_404(db: Session, draft_id: int) -> Draft:
    draft = db.get(Draft, draft_id)
    if not draft:
        raise DraftServiceError(f"Draft {draft_id} not found.")
    return draft


def edit_draft(db: Session, draft_id: int, subject: str, body: str) -> Draft:
    draft = get_draft_or_404(db, draft_id)
    subject = (subject or "").strip()
    body = (body or "").strip()
    if not subject or not body:
        raise DraftServiceError("Subject and body are both required.")

    latest = draft.latest_version()

    return _persist_new_version(
        db,
        draft.lead,
        subject=subject,
        body=body,
        language=latest.language if latest else DEFAULT_LANGUAGE,
        tone=latest.tone if latest else DEFAULT_TONE,
        length=latest.length if latest else DEFAULT_LENGTH,
        claim_sources=[],
        review_flags=[
            {"type": "manually_edited", "detail": "Content was manually edited; claim sources were not re-verified against this text."}
        ],
        is_generic_fallback=latest.is_generic_fallback if latest else False,
        research_version_snapshot=latest.research_version_snapshot if latest else 0,
        criteria_version_snapshot=latest.criteria_version_snapshot if latest else None,
        prompt_version=PROMPT_VERSION,
        model="manual-edit",
        prompt_tokens=None,
        completion_tokens=None,
        total_tokens=None,
        edited_manually=True,
    )


def approve_draft(db: Session, draft_id: int) -> Draft:
    draft = get_draft_or_404(db, draft_id)
    if not draft.lead.email:
        raise DraftServiceError("Cannot approve a draft for a lead with no email address.")

    recipient = draft.lead.email

    # Atomic: the UPDATE only applies if version/subject/body still match
    # exactly what was just read. If a concurrent edit or a background
    # regeneration lands between this read and the write, rowcount is 0 and
    # approval is correctly refused rather than binding to stale content.
    stmt = (
        update(Draft)
        .where(Draft.id == draft_id, Draft.version == draft.version, Draft.subject == draft.subject, Draft.body == draft.body)
        .values(
            approval_status="approved",
            approved_version=draft.version,
            approved_subject=draft.subject,
            approved_body=draft.body,
            approved_recipient_email=recipient,
            approved_at=datetime.now(timezone.utc),
        )
    )
    result = db.execute(stmt)
    if result.rowcount != 1:
        db.rollback()
        raise DraftServiceError("This draft changed while you were approving it; please review the latest version and try again.")

    draft.lead.status = "approved"
    db.commit()
    db.refresh(draft)
    return draft


def reject_draft(db: Session, draft_id: int) -> Draft:
    draft = get_draft_or_404(db, draft_id)
    _reset_approval(draft)
    draft.approval_status = "rejected"
    draft.lead.status = "rejected"

    db.commit()
    db.refresh(draft)
    return draft


def get_draft_history(db: Session, draft_id: int) -> list[DraftVersion]:
    draft = get_draft_or_404(db, draft_id)
    return list(draft.versions)
