"""添加 Wiki 文档表 (wikis, wiki_sections, wiki_pages)

Revision ID: 003
Revises: 002
Create Date: 2026-02-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 创建 wikis 表
    op.create_table(
        "wikis",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("outline_json", sa.JSON(), nullable=True),
        sa.Column("llm_provider", sa.String(64), nullable=True),
        sa.Column("llm_model", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wikis_repo_id", "wikis", ["repo_id"])

    # 创建 wiki_sections 表
    op.create_table(
        "wiki_sections",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("wiki_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["wiki_id"], ["wikis.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wiki_sections_wiki_id", "wiki_sections", ["wiki_id"])

    # 创建 wiki_pages 表
    op.create_table(
        "wiki_pages",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("section_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("importance", sa.String(16), nullable=True),
        sa.Column("content_md", sa.Text(), nullable=True),
        sa.Column("relevant_files", sa.JSON(), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["section_id"], ["wiki_sections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wiki_pages_section_id", "wiki_pages", ["section_id"])


def downgrade() -> None:
    op.drop_index("ix_wiki_pages_section_id", table_name="wiki_pages")
    op.drop_table("wiki_pages")
    op.drop_index("ix_wiki_sections_wiki_id", table_name="wiki_sections")
    op.drop_table("wiki_sections")
    op.drop_index("ix_wikis_repo_id", table_name="wikis")
    op.drop_table("wikis")
