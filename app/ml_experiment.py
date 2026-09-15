"""Optional ML experiment: predicting the HUMAN SUITABILITY LABEL a reviewer
assigned (suitable / unsuitable / unsure), NOT sales conversion, NOT a
replacement for the deterministic qualification baseline in
qualification_service.py.

This module is intentionally split into two phases:

1. `assess_label_readiness(db)` -- always safe to run. Reports how much
   labeled data actually exists (sample count, class balance, unique
   companies, campaign diversity) and whether it can support a meaningful
   *grouped* evaluation.

   Grouping is by COMPANY IDENTITY, not by campaign: the same company can
   legitimately appear in several campaigns (re-imported, re-targeted, etc.),
   and if the same company's leads end up on both sides of a train/test
   split, the model can "cheat" by memorizing that company rather than
   learning anything transferable -- campaign membership does not prevent
   that leak at all. See `_company_group_key`.

   Sample counts alone do not prove readiness either: a class can clear a
   raw sample-count threshold while all of those samples come from one or
   two companies (e.g. the same company re-labeled across campaigns), which
   is not meaningfully different from having one sample. So readiness also
   requires each class to be backed by enough *distinct* company groups, and
   no single company group to dominate the labeled set.

2. `train_suitability_model(db)` -- only trains if step 1 says the data is
   sufficient. If not, it raises InsufficientLabelDataError carrying the same
   assessment, so callers can surface *why* training was deferred instead of
   silently training on (or worse, "successfully" reporting metrics for) a
   dataset too small or too concentrated to trust.

Features are the deterministic qualification's own explainable outputs
(fit_score, evidence_coverage, and matched/unmatched/unknown/exclusion
counts) -- not raw scraped text -- so the candidate model stays auditable and
consistent with the rest of this milestone's explainability requirements.
"""
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from database import Lead, Qualification, QualificationLabel
from modules.csv_handler import normalize_website

MIN_TOTAL_SAMPLES = 30
MIN_SAMPLES_PER_CLASS = 8
MIN_COMPANY_GROUPS = 10  # distinct companies overall, across all labels
MIN_COMPANY_GROUPS_PER_CLASS = 3  # each class must span at least this many distinct companies
MAX_SINGLE_GROUP_SHARE = 0.4  # no single company may exceed this fraction of all labeled samples

FEATURE_NAMES = [
    "fit_score",
    "evidence_coverage",
    "num_matched",
    "num_unmatched",
    "num_unknown",
    "num_exclusions",
]

LABELS = ["suitable", "unsuitable", "unsure"]


class InsufficientLabelDataError(Exception):
    def __init__(self, assessment: "LabelDataAssessment"):
        self.assessment = assessment
        super().__init__("; ".join(assessment.reasons) or "insufficient labeled data")


@dataclass
class LabelDataAssessment:
    total_labeled_leads: int
    class_counts: dict
    unique_companies: int
    unique_campaigns: int
    sufficient_for_training: bool
    reasons: list = field(default_factory=list)


def _company_group_key(lead: Lead) -> str:
    """Stable identity used both to report company diversity and to group
    samples for train/test splitting, so a company can never end up on both
    sides of a split just because it was imported into a second campaign.

    Preferred identity: the lead's explicit website domain, normalized the
    same way as at CSV-import time (so "https://Acme.com/" and "acme.com"
    collapse to the same key). Documented fallback, only when no website is
    on record: the normalized company name -- weaker (two different
    companies that happen to share a name would incorrectly collapse into
    one group), but still far better than no grouping at all, and it errs on
    the side of caution for leakage (over-grouping loses potential training
    signal; under-grouping leaks eval data -- the latter is worse).
    """
    if lead.website:
        return f"domain:{normalize_website(lead.website)}"
    return f"name:{(lead.company or '').strip().lower()}"


def _latest_label_per_lead(db: Session) -> dict:
    """Returns {lead_id: QualificationLabel} using each lead's most recent label."""
    latest = {}
    for label in db.query(QualificationLabel).order_by(QualificationLabel.labeled_at).all():
        latest[label.lead_id] = label  # later rows overwrite earlier ones -> keeps the latest
    return latest


def assess_label_readiness(db: Session) -> LabelDataAssessment:
    latest_by_lead = _latest_label_per_lead(db)
    total = len(latest_by_lead)

    class_counts = {name: 0 for name in LABELS}
    company_groups_by_class = {name: set() for name in LABELS}
    all_company_groups = set()
    campaigns = set()
    group_sample_counts = Counter()

    for lead_id, label in latest_by_lead.items():
        lead = db.get(Lead, lead_id)
        if not lead:
            continue
        class_counts[label.label] = class_counts.get(label.label, 0) + 1
        group_key = _company_group_key(lead)
        all_company_groups.add(group_key)
        company_groups_by_class[label.label].add(group_key)
        group_sample_counts[group_key] += 1
        campaigns.add(lead.campaign_id)

    reasons = []
    if total < MIN_TOTAL_SAMPLES:
        reasons.append(f"only {total} labeled lead(s) (need at least {MIN_TOTAL_SAMPLES})")

    missing_classes = [name for name in LABELS if class_counts.get(name, 0) == 0]
    if missing_classes:
        reasons.append(f"no labeled examples at all for: {', '.join(missing_classes)}")

    under_represented = [
        name for name in LABELS if 0 < class_counts.get(name, 0) < MIN_SAMPLES_PER_CLASS
    ]
    if under_represented:
        reasons.append(
            f"too few examples (< {MIN_SAMPLES_PER_CLASS}) for: "
            + ", ".join(f"{name} ({class_counts[name]})" for name in under_represented)
        )

    if len(all_company_groups) < MIN_COMPANY_GROUPS:
        reasons.append(
            f"only {len(all_company_groups)} distinct compan{'y' if len(all_company_groups) == 1 else 'ies'} "
            f"labeled overall (need at least {MIN_COMPANY_GROUPS} to hold out unseen companies for evaluation)"
        )

    thin_classes = [
        name
        for name in LABELS
        if class_counts.get(name, 0) > 0 and len(company_groups_by_class[name]) < MIN_COMPANY_GROUPS_PER_CLASS
    ]
    if thin_classes:
        reasons.append(
            f"class(es) backed by too few distinct companies (< {MIN_COMPANY_GROUPS_PER_CLASS}), so their sample "
            f"count doesn't prove the model would generalize: "
            + ", ".join(f"{name} ({len(company_groups_by_class[name])} compan(y/ies))" for name in thin_classes)
        )

    if total > 0 and group_sample_counts:
        dominant_group, dominant_count = group_sample_counts.most_common(1)[0]
        share = dominant_count / total
        if share > MAX_SINGLE_GROUP_SHARE:
            reasons.append(
                f"one company ({dominant_group}) accounts for {share:.0%} of all labeled samples "
                f"(> {MAX_SINGLE_GROUP_SHARE:.0%} limit) -- too concentrated to trust a held-out split"
            )

    return LabelDataAssessment(
        total_labeled_leads=total,
        class_counts=class_counts,
        unique_companies=len(all_company_groups),
        unique_campaigns=len(campaigns),
        sufficient_for_training=(len(reasons) == 0),
        reasons=reasons,
    )


def extract_features(qualification: Qualification) -> list:
    return [
        qualification.fit_score if qualification.fit_score is not None else 0.0,
        qualification.evidence_coverage,
        len(qualification.matched),
        len(qualification.unmatched),
        len(qualification.unknown),
        len(qualification.exclusions),
    ]


def build_training_table(db: Session):
    """Returns (X, y, groups) for the leads whose CURRENT qualification is
    exactly the one the most recent label reviewed (criteria_version and
    research_version_snapshot both match what was recorded at label time) --
    a lead whose research/criteria changed since it was labeled is excluded
    rather than paired with mismatched features.

    `groups` is the company identity from `_company_group_key`, NOT the
    campaign id -- this is what GroupKFold splits on, so the same company
    can never appear in both the train and the held-out fold just because it
    was imported into more than one campaign.
    """
    X, y, groups = [], [], []
    for lead_id, label in _latest_label_per_lead(db).items():
        lead = db.get(Lead, lead_id)
        qualification = lead.qualification if lead else None
        if not qualification:
            continue
        if (
            qualification.criteria_version != label.criteria_version_reviewed
            or qualification.research_version_snapshot != label.research_version_reviewed
        ):
            continue
        X.append(extract_features(qualification))
        y.append(label.label)
        groups.append(_company_group_key(lead))
    return X, y, groups


def train_suitability_model(db: Session, *, n_splits: Optional[int] = None) -> dict:
    """Trains an sklearn LogisticRegression to predict the human suitability
    label from qualification features, evaluated with GroupKFold grouped by
    COMPANY IDENTITY (see _company_group_key) so no fold sees labels from a
    company it's being scored on -- including when that company shows up
    under a different campaign. Raises InsufficientLabelDataError (never
    trains, never fabricates a metric) if assess_label_readiness() says the
    data can't support it."""
    assessment = assess_label_readiness(db)
    if not assessment.sufficient_for_training:
        raise InsufficientLabelDataError(assessment)

    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import GroupKFold, cross_val_score

    X, y, groups = build_training_table(db)
    n_unique_groups = len(set(groups))
    n_splits = n_splits or min(5, n_unique_groups)

    model = LogisticRegression(max_iter=1000)
    cv = GroupKFold(n_splits=n_splits)
    scores = cross_val_score(model, X, y, groups=groups, cv=cv, scoring="balanced_accuracy")

    model.fit(X, y)  # final model fit on all available data, for actual use

    return {
        "model": model,
        "feature_names": FEATURE_NAMES,
        "cv_balanced_accuracy_per_fold": scores.tolist(),
        "cv_balanced_accuracy_mean": float(scores.mean()),
        "n_samples": len(y),
        "n_splits": n_splits,
        "assessment": assessment,
    }
