"""add evaluation run and result tables

Revision ID: a1b2c3d4e5f6
Revises: 3e6c91d9144f
Create Date: 2026-09-22 09:00:00.000000

Purely additive: adds `eval_runs` and `eval_results` for the offline/live
drafting-comparison evaluation harness (app/evaluation/). No existing
table, column, or row is touched. Entirely separate from `leads` and
`qualification_labels` -- evaluation cases are synthetic fixtures, never
real leads, and human ratings here are never used as ML training labels.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '3e6c91d9144f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("dataset_version", sa.String, nullable=False),
        sa.Column("mode", sa.String, nullable=False),
        sa.Column("model_basic", sa.String, nullable=False),
        sa.Column("model_evidence_based", sa.String, nullable=False),
        sa.Column("prompt_version_basic", sa.String, nullable=False),
        sa.Column("prompt_version_evidence_based", sa.String, nullable=False),
        sa.Column("language", sa.String, nullable=False),
        sa.Column("tone", sa.String, nullable=False),
        sa.Column("length", sa.String, nullable=False),
        sa.Column("max_calls", sa.Integer, nullable=True),
        sa.Column("calls_made", sa.Integer, nullable=False, server_default="0"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "eval_results",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("eval_run_id", sa.Integer, sa.ForeignKey("eval_runs.id"), nullable=False),
        sa.Column("case_id", sa.String, nullable=False),
        sa.Column("case_tags", sa.JSON, nullable=False),
        sa.Column("approach", sa.String, nullable=False),
        sa.Column("subject", sa.Text, nullable=True),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("raw_output", sa.JSON, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("latency_ms", sa.Float, nullable=True),
        sa.Column("prompt_tokens", sa.Integer, nullable=True),
        sa.Column("completion_tokens", sa.Integer, nullable=True),
        sa.Column("total_tokens", sa.Integer, nullable=True),
        sa.Column("metrics", sa.JSON, nullable=False),
        sa.Column("model_judge", sa.JSON, nullable=True),
        sa.Column("human_rating_relevance", sa.Integer, nullable=True),
        sa.Column("human_rating_personalization", sa.Integer, nullable=True),
        sa.Column("human_notes", sa.Text, nullable=True),
        sa.Column("human_rated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("eval_run_id", "case_id", "approach", name="uq_eval_result_run_case_approach"),
    )
    op.create_index("ix_eval_results_eval_run_id", "eval_results", ["eval_run_id"])
    op.create_index("ix_eval_results_case_id", "eval_results", ["case_id"])


def downgrade() -> None:
    op.drop_table("eval_results")
    op.drop_table("eval_runs")
