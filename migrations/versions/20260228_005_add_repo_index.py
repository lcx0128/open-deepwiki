"""添加 repo_indexes 表（代码库索引）

Revision ID: 005
Revises: 004
Create Date: 2026-02-28 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "repo_indexes",
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("index_json", sa.JSON(), nullable=True),
        sa.Column("generated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("repo_id"),
    )


def downgrade() -> None:
    op.drop_table("repo_indexes")
