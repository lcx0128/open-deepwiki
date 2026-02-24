"""tasks 表新增 failed_at_stage 字段

Revision ID: 002
Revises: 001
Create Date: 2026-02-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "failed_at_stage",
            sa.String(32),
            nullable=True,
            comment="失败时所处阶段：cloning / parsing / embedding / generating",
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "failed_at_stage")
