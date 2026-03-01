"""为 wiki_pages 表新增 summary 和 page_type 字段

Revision ID: 006
Revises: 005
Create Date: 2026-03-01 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("wiki_pages", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("wiki_pages", sa.Column("page_type", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("wiki_pages", "page_type")
    op.drop_column("wiki_pages", "summary")
