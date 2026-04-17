"""add freshness_signal column to jobs

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-17
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite requires batch mode for ALTER TABLE.
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.add_column(
            sa.Column("freshness_signal", sa.Float(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_column("freshness_signal")
