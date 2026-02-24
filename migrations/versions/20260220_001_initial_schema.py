"""初始数据库 Schema

Revision ID: 001
Revises:
Create Date: 2026-02-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 创建 repositories 表
    op.create_table(
        "repositories",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "platform",
            sa.Enum("github", "gitlab", "bitbucket", "custom", name="repoplatform"),
            nullable=False,
        ),
        sa.Column("default_branch", sa.String(128), nullable=True),
        sa.Column("local_path", sa.String(1024), nullable=True),
        sa.Column(
            "status",
            sa.Enum("pending", "cloning", "ready", "error", "syncing", name="repostatus"),
            nullable=False,
        ),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )

    # 创建 tasks 表
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "full_process", "incremental_sync", "wiki_regenerate", "parse_only",
                name="tasktype",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "cloning", "parsing", "embedding", "generating",
                "completed", "failed", "cancelled",
                name="taskstatus",
            ),
            nullable=False,
        ),
        sa.Column("progress_pct", sa.Float(), nullable=False),
        sa.Column("current_stage", sa.String(64), nullable=True),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("celery_task_id", sa.String(255), nullable=True),
        sa.Column("files_total", sa.Integer(), nullable=True),
        sa.Column("files_processed", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_repo_id", "tasks", ["repo_id"])

    # 创建 file_states 表
    op.create_table(
        "file_states",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("repo_id", sa.String(36), nullable=False),
        sa.Column("file_path", sa.String(1024), nullable=False),
        sa.Column("last_commit_hash", sa.String(40), nullable=False),
        sa.Column("chunk_ids_json", sa.Text(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=True),
        sa.Column("file_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repo_id", "file_path", name="uq_file_state_repo_path"),
    )
    op.create_index("ix_file_states_repo_id", "file_states", ["repo_id"])


def downgrade() -> None:
    op.drop_index("ix_file_states_repo_id", table_name="file_states")
    op.drop_table("file_states")
    op.drop_index("ix_tasks_repo_id", table_name="tasks")
    op.drop_table("tasks")
    op.drop_table("repositories")
