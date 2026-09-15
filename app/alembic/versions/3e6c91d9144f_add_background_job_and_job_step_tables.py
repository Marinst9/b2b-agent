"""add background job and job step tables

Revision ID: 3e6c91d9144f
Revises: 069e38328f0d
Create Date: 2026-09-14 09:00:00.000000

Purely additive: adds `jobs` and `job_steps`. No existing table, column, or
row is touched.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3e6c91d9144f'
down_revision: Union[str, Sequence[str], None] = '069e38328f0d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("campaign_id", sa.Integer, sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("job_type", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="queued"),
        sa.Column("params", sa.JSON, nullable=False),
        sa.Column("dispatched", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("dispatch_attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("celery_task_id", sa.String, nullable=True),
        sa.Column("idempotency_key", sa.String, nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_jobs_campaign_id", "jobs", ["campaign_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])

    op.create_table(
        "job_steps",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("job_id", sa.Integer, sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("lead_id", sa.Integer, sa.ForeignKey("leads.id"), nullable=False),
        sa.Column("step_type", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="queued"),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"),
        sa.Column("lease_owner", sa.String, nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_version_snapshot", sa.JSON, nullable=False),
        sa.Column("result_summary", sa.JSON, nullable=True),
        sa.Column("last_error", sa.String, nullable=True),
        sa.Column("celery_task_id", sa.String, nullable=True),
        sa.Column("idempotency_key", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("job_id", "lead_id", "step_type", name="uq_job_step_lead_type"),
    )
    op.create_index("ix_job_steps_job_id", "job_steps", ["job_id"])
    op.create_index("ix_job_steps_lead_id", "job_steps", ["lead_id"])
    op.create_index("ix_job_steps_status", "job_steps", ["status"])


def downgrade() -> None:
    op.drop_table("job_steps")
    op.drop_table("jobs")
