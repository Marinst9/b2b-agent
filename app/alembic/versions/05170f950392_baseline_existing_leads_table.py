"""baseline existing leads table

Revision ID: 05170f950392
Revises: 
Create Date: 2026-09-13 13:01:56.681421

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '05170f950392'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Represents the `leads` table as it existed before Alembic was introduced.

    On a fresh database (e.g. a new dev setup or the test suite) this creates that
    original table. On the project's existing database, which already has this
    exact table with data, this revision is applied via `alembic stamp` instead of
    being executed, so existing rows are never touched by it.
    """
    op.create_table(
        "leads",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("name", sa.String),
        sa.Column("company", sa.String),
        sa.Column("industry", sa.String),
        sa.Column("country", sa.String),
        sa.Column("email", sa.String),
        sa.Column("message", sa.Text),
        sa.Column("status", sa.String, server_default="waiting"),
        sa.Column("opened", sa.Boolean, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_table("leads")
