"""add campaigns and drafts, backfill legacy leads

Revision ID: da129dd86883
Revises: 05170f950392
Create Date: 2026-09-13 13:02:09.517992

This migration adds the `campaigns`, `drafts`, and `send_attempts` tables and
extends `leads` with `campaign_id`, `website`, and `dedup_key`. Pre-existing
leads (which predate campaigns) are assigned to a single backfilled "Legacy
Import" campaign so none of their data is lost, and any lead that already has
a generated `message` (from the old inline send flow) gets a corresponding
draft row (pending approval) so that history is preserved instead of dropped.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'da129dd86883'
down_revision: Union[str, Sequence[str], None] = '05170f950392'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    campaigns_table = sa.table(
        "campaigns",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
    )

    with op.batch_alter_table("leads") as batch_op:
        batch_op.add_column(sa.Column("campaign_id", sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("website", sa.String, nullable=True))
        batch_op.add_column(sa.Column("dedup_key", sa.String, nullable=True))
        batch_op.add_column(
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now())
        )

    leads_table = sa.table(
        "leads",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("company", sa.String),
        sa.column("email", sa.String),
        sa.column("message", sa.Text),
        sa.column("campaign_id", sa.Integer),
        sa.column("dedup_key", sa.String),
        sa.column("status", sa.String),
    )

    existing_lead_rows = bind.execute(
        sa.select(leads_table.c.id, leads_table.c.name, leads_table.c.company, leads_table.c.email, leads_table.c.message)
    ).fetchall()

    if existing_lead_rows:
        bind.execute(campaigns_table.insert().values(name="Legacy Import"))
        legacy_campaign_id = bind.execute(
            sa.select(campaigns_table.c.id)
            .where(campaigns_table.c.name == "Legacy Import")
            .order_by(campaigns_table.c.id.desc())
        ).scalar_one()

        # Pre-existing rows predate duplicate-import enforcement, so real historical
        # duplicates (e.g. the old "test@example.com" fallback, or leads imported more
        # than once) can legitimately collide on the normal dedup key. Every row is
        # still kept -- a colliding key is disambiguated with the row's own id so the
        # new unique constraint can be added without dropping or merging any data.
        used_keys = set()
        for row in existing_lead_rows:
            base_key = (
                f"email:{row.email.strip().lower()}"
                if row.email
                else f"namecompany:{(row.name or '').strip().lower()}|{(row.company or '').strip().lower()}"
            )
            dedup_key = base_key
            if dedup_key in used_keys:
                dedup_key = f"{base_key}|legacy-{row.id}"
            used_keys.add(dedup_key)

            bind.execute(
                leads_table.update()
                .where(leads_table.c.id == row.id)
                .values(campaign_id=legacy_campaign_id, dedup_key=dedup_key)
            )

    with op.batch_alter_table("leads") as batch_op:
        batch_op.alter_column("campaign_id", nullable=False)
        batch_op.alter_column("dedup_key", nullable=False)
        batch_op.create_foreign_key(
            "fk_leads_campaign_id", "campaigns", ["campaign_id"], ["id"]
        )
        batch_op.create_unique_constraint(
            "uq_lead_campaign_dedup", ["campaign_id", "dedup_key"]
        )

    op.create_table(
        "drafts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("lead_id", sa.Integer, sa.ForeignKey("leads.id"), nullable=False, unique=True),
        sa.Column("version", sa.Integer, nullable=False, default=1),
        sa.Column("subject", sa.String, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("approval_status", sa.String, nullable=False, server_default="pending"),
        sa.Column("approved_version", sa.Integer, nullable=True),
        sa.Column("approved_recipient_email", sa.String, nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "send_attempts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("lead_id", sa.Integer, sa.ForeignKey("leads.id"), nullable=False),
        sa.Column("draft_version", sa.Integer, nullable=False),
        sa.Column("recipient_email", sa.String, nullable=False),
        sa.Column("dry_run", sa.Boolean, nullable=False, default=True),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("error", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    drafts_table = sa.table(
        "drafts",
        sa.column("lead_id", sa.Integer),
        sa.column("version", sa.Integer),
        sa.column("subject", sa.String),
        sa.column("body", sa.Text),
        sa.column("approval_status", sa.String),
    )

    for row in existing_lead_rows:
        if row.message:
            bind.execute(
                drafts_table.insert().values(
                    lead_id=row.id,
                    version=1,
                    subject=f"Соработка со {row.company or ''}".strip(),
                    body=row.message,
                    approval_status="pending",
                )
            )


def downgrade() -> None:
    op.drop_table("send_attempts")
    op.drop_table("drafts")

    with op.batch_alter_table("leads") as batch_op:
        batch_op.drop_constraint("uq_lead_campaign_dedup", type_="unique")
        batch_op.drop_constraint("fk_leads_campaign_id", type_="foreignkey")
        batch_op.drop_column("campaign_id")
        batch_op.drop_column("website")
        batch_op.drop_column("dedup_key")
        batch_op.drop_column("created_at")

    op.drop_table("campaigns")
