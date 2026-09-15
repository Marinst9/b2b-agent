from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from database import Campaign, Lead, SendAttempt
from modules.csv_handler import validate_and_parse_csv, CSVValidationError
from modules import enrichment, email_sender


class ServiceError(Exception):
    """Raised for request-level errors that should map to a 4xx HTTP response."""


def compute_dedup_key(email: str | None, name: str, company: str) -> str:
    if email:
        return f"email:{email.strip().lower()}"
    return f"namecompany:{name.strip().lower()}|{company.strip().lower()}"


def create_campaign(db: Session, name: str) -> Campaign:
    name = (name or "").strip()
    if not name:
        raise ServiceError("Campaign name is required.")
    campaign = Campaign(name=name)
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return campaign


def get_campaign_or_404(db: Session, campaign_id: int) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise ServiceError(f"Campaign {campaign_id} not found.")
    return campaign


def import_leads(db: Session, campaign_id: int, contents: bytes) -> dict:
    get_campaign_or_404(db, campaign_id)

    try:
        parsed = validate_and_parse_csv(contents)
    except CSVValidationError as e:
        raise ServiceError(str(e))

    existing_keys = {
        lead.dedup_key
        for lead in db.query(Lead).filter(Lead.campaign_id == campaign_id).all()
    }

    imported = 0
    duplicates = 0
    row_errors = [e.as_dict() for e in parsed.row_errors]
    seen_in_file = set()

    for row in parsed.valid_rows:
        dedup_key = compute_dedup_key(row["email"], row["name"], row["company"])

        if dedup_key in existing_keys or dedup_key in seen_in_file:
            duplicates += 1
            row_errors.append(
                {
                    "row": row["row"],
                    "field": "email" if row["email"] else "name/company",
                    "message": "Duplicate lead within this campaign; skipped.",
                }
            )
            continue

        email = row["email"]
        if not email and row["website"]:
            name_parts = row["name"].split()
            first_name = name_parts[0] if name_parts else ""
            last_name = name_parts[1] if len(name_parts) > 1 else ""
            try:
                found = enrichment.get_email(first_name, last_name, row["website"])
                email = found.get("email") or None
            except Exception:
                email = None

        lead = Lead(
            campaign_id=campaign_id,
            name=row["name"],
            company=row["company"],
            industry=row["industry"],
            country=row["country"],
            website=row["website"],
            email=email,
            dedup_key=dedup_key,
            status="new",
        )
        db.add(lead)
        seen_in_file.add(dedup_key)
        imported += 1

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ServiceError("Import failed due to a duplicate-key conflict. No rows were saved; please retry.")

    return {
        "total_rows": parsed.total_rows,
        "imported": imported,
        "duplicates": duplicates,
        "row_errors": row_errors,
    }


def send_campaign(db: Session, campaign_id: int, dry_run: bool = True, lead_ids: list[int] | None = None) -> dict:
    get_campaign_or_404(db, campaign_id)

    query = db.query(Lead).filter(Lead.campaign_id == campaign_id)
    if lead_ids:
        query = query.filter(Lead.id.in_(lead_ids))
    leads = query.all()

    sent = 0
    skipped = 0
    failed = 0
    details = []

    for lead in leads:
        draft = lead.draft
        if not draft or not draft.is_approved_for_send():
            skipped += 1
            details.append({"lead_id": lead.id, "result": "skipped", "reason": "no approved draft for the current recipient/version"})
            continue

        if dry_run:
            success = True
            error = None
        else:
            try:
                success = email_sender.send_email(lead.email, draft.subject, draft.body)
                error = None if success else "send_email returned failure"
            except Exception as e:
                success = False
                error = str(e)

        db.add(
            SendAttempt(
                lead_id=lead.id,
                draft_version=draft.version,
                recipient_email=lead.email,
                dry_run=dry_run,
                success=success,
                error=error,
            )
        )

        if success:
            lead.status = "dry_run_sent" if dry_run else "sent"
            sent += 1
            details.append({"lead_id": lead.id, "result": "dry_run_sent" if dry_run else "sent"})
        else:
            lead.status = "send_failed"
            failed += 1
            details.append({"lead_id": lead.id, "result": "failed", "reason": error})

    db.commit()
    return {"sent": sent, "skipped": skipped, "failed": failed, "dry_run": dry_run, "details": details}


def get_stats(db: Session, campaign_id: int | None = None) -> dict:
    query = db.query(Lead)
    if campaign_id is not None:
        query = query.filter(Lead.campaign_id == campaign_id)
    leads = query.all()

    total = len(leads)
    by_status = {}
    by_industry = {}
    for lead in leads:
        by_status[lead.status] = by_status.get(lead.status, 0) + 1
        industry = lead.industry or "Unknown"
        by_industry[industry] = by_industry.get(industry, 0) + 1

    return {
        "total_leads": total,
        "drafted": by_status.get("drafted", 0),
        "approved": by_status.get("approved", 0),
        "sent": by_status.get("sent", 0),
        "dry_run_sent": by_status.get("dry_run_sent", 0),
        "send_failed": by_status.get("send_failed", 0),
        "rejected": by_status.get("rejected", 0),
        "by_industry": by_industry,
        "opened": None,
        "replied": None,
        "tracking_available": {"opened": False, "replied": False},
    }
