"""add company research tables

Revision ID: 73105722ed66
Revises: da129dd86883
Create Date: 2026-09-13 17:18:21.786985

Purely additive: adds `company_research` and `research_facts`. No existing
table, column, or row is touched, so this migration cannot lose data.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '73105722ed66'
down_revision: Union[str, Sequence[str], None] = 'da129dd86883'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "company_research",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("lead_id", sa.Integer, sa.ForeignKey("leads.id"), nullable=False, unique=True),
        sa.Column("website", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column("error", sa.String, nullable=True),
        sa.Column("pages_fetched", sa.Integer, server_default="0"),
        sa.Column("offerings_unknown", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("target_customers_unknown", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("has_conflicts", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refresh_error", sa.String, nullable=True),
        sa.Column("last_refresh_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "research_facts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("research_id", sa.Integer, sa.ForeignKey("company_research.id"), nullable=False),
        sa.Column("category", sa.String, nullable=False),
        sa.Column("key", sa.String, nullable=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("excerpt", sa.Text, nullable=False),
        sa.Column("source_url", sa.String, nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("conflicting", sa.Boolean, nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_table("research_facts")
    op.drop_table("company_research")
