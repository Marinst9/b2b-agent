"""add draft version history and approval snapshot fields

Revision ID: 069e38328f0d
Revises: 5cd9747b74f1
Create Date: 2026-09-14 10:00:00.000000

Adds `draft_versions` (immutable history) and two new nullable columns on
`drafts` (`approved_subject`, `approved_body`). No existing table, column, or
row is dropped or altered destructively. Every existing Draft row is
backfilled with exactly one DraftVersion capturing its current content, so
history is never lost; any existing approved draft gets its approved_subject/
approved_body filled in to match its current content (which, under the prior
overwrite-in-place system, was always the same as whatever was approved).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '069e38328f0d'
down_revision: Union[str, Sequence[str], None] = '5cd9747b74f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    with op.batch_alter_table("drafts") as batch_op:
        batch_op.add_column(sa.Column("approved_subject", sa.String, nullable=True))
        batch_op.add_column(sa.Column("approved_body", sa.Text, nullable=True))

    op.create_table(
        "draft_versions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("draft_id", sa.Integer, sa.ForeignKey("drafts.id"), nullable=False),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("subject", sa.String, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("language", sa.String, nullable=False, server_default="mk"),
        sa.Column("tone", sa.String, nullable=False, server_default="professional"),
        sa.Column("length", sa.String, nullable=False, server_default="medium"),
        sa.Column("claim_sources", sa.JSON, nullable=False),
        sa.Column("review_flags", sa.JSON, nullable=False),
        sa.Column("is_generic_fallback", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("research_version_snapshot", sa.Integer, nullable=False, server_default="0"),
        sa.Column("criteria_version_snapshot", sa.Integer, nullable=True),
        sa.Column("prompt_version", sa.String, nullable=False),
        sa.Column("model", sa.String, nullable=False),
        sa.Column("prompt_tokens", sa.Integer, nullable=True),
        sa.Column("completion_tokens", sa.Integer, nullable=True),
        sa.Column("total_tokens", sa.Integer, nullable=True),
        sa.Column("edited_manually", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("draft_id", "version_number", name="uq_draft_version_number"),
    )

    drafts_table = sa.table(
        "drafts",
        sa.column("id", sa.Integer),
        sa.column("version", sa.Integer),
        sa.column("subject", sa.String),
        sa.column("body", sa.Text),
        sa.column("approval_status", sa.String),
        sa.column("approved_subject", sa.String),
        sa.column("approved_body", sa.Text),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    draft_versions_table = sa.table(
        "draft_versions",
        sa.column("draft_id", sa.Integer),
        sa.column("version_number", sa.Integer),
        sa.column("subject", sa.String),
        sa.column("body", sa.Text),
        sa.column("claim_sources", sa.JSON),
        sa.column("review_flags", sa.JSON),
        sa.column("is_generic_fallback", sa.Boolean),
        sa.column("research_version_snapshot", sa.Integer),
        sa.column("prompt_version", sa.String),
        sa.column("model", sa.String),
        sa.column("edited_manually", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    existing_drafts = bind.execute(
        sa.select(
            drafts_table.c.id,
            drafts_table.c.version,
            drafts_table.c.subject,
            drafts_table.c.body,
            drafts_table.c.approval_status,
            drafts_table.c.created_at,
        )
    ).fetchall()

    for row in existing_drafts:
        bind.execute(
            draft_versions_table.insert().values(
                draft_id=row.id,
                version_number=row.version,
                subject=row.subject,
                body=row.body,
                claim_sources=[],
                review_flags=[
                    {
                        "type": "legacy_draft",
                        "detail": "Created before evidence-based drafting; no claim sources or generation metadata were recorded.",
                    }
                ],
                is_generic_fallback=True,
                research_version_snapshot=0,
                prompt_version="legacy",
                model="legacy",
                edited_manually=False,
                created_at=row.created_at,
            )
        )
        if row.approval_status == "approved":
            bind.execute(
                drafts_table.update()
                .where(drafts_table.c.id == row.id)
                .values(approved_subject=row.subject, approved_body=row.body)
            )


def downgrade() -> None:
    op.drop_table("draft_versions")
    with op.batch_alter_table("drafts") as batch_op:
        batch_op.drop_column("approved_subject")
        batch_op.drop_column("approved_body")
