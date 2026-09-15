"""add qualification criteria, results, and labels

Revision ID: 5cd9747b74f1
Revises: 73105722ed66
Create Date: 2026-09-13 19:00:00.000000

Purely additive: adds `campaign_criteria`, `qualifications`,
`qualification_labels`, and a `version` column (default 0) on
`company_research`. No existing table, column, or row is modified or dropped.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5cd9747b74f1'
down_revision: Union[str, Sequence[str], None] = '73105722ed66'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("company_research") as batch_op:
        batch_op.add_column(sa.Column("version", sa.Integer, nullable=False, server_default="0"))

    op.create_table(
        "campaign_criteria",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("campaign_id", sa.Integer, sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("product_service", sa.Text, nullable=True),
        sa.Column("target_industries", sa.JSON, nullable=False),
        sa.Column("target_countries", sa.JSON, nullable=False),
        sa.Column("preferred_company_size", sa.String, nullable=True),
        sa.Column("business_needs", sa.JSON, nullable=False),
        sa.Column("exclusion_criteria", sa.JSON, nullable=False),
        sa.Column("weights", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("campaign_id", "version", name="uq_campaign_criteria_version"),
    )

    op.create_table(
        "qualifications",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("lead_id", sa.Integer, sa.ForeignKey("leads.id"), nullable=False, unique=True),
        sa.Column("criteria_id", sa.Integer, sa.ForeignKey("campaign_criteria.id"), nullable=False),
        sa.Column("criteria_version", sa.Integer, nullable=False),
        sa.Column("research_version_snapshot", sa.Integer, nullable=False),
        sa.Column("rubric_version", sa.String, nullable=False),
        sa.Column("fit_score", sa.Float, nullable=True),
        sa.Column("evidence_coverage", sa.Float, nullable=False),
        sa.Column("matched", sa.JSON, nullable=False),
        sa.Column("unmatched", sa.JSON, nullable=False),
        sa.Column("unknown", sa.JSON, nullable=False),
        sa.Column("exclusions", sa.JSON, nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "qualification_labels",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("lead_id", sa.Integer, sa.ForeignKey("leads.id"), nullable=False),
        sa.Column("label", sa.String, nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("criteria_version_reviewed", sa.Integer, nullable=False),
        sa.Column("research_version_reviewed", sa.Integer, nullable=False),
        sa.Column("labeled_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("qualification_labels")
    op.drop_table("qualifications")
    op.drop_table("campaign_criteria")
    with op.batch_alter_table("company_research") as batch_op:
        batch_op.drop_column("version")
