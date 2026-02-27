"""添加 INTERRUPTED 任务/仓库状态，支持中止与恢复

Revision ID: 004
Revises: 003
Create Date: 2026-02-27 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 旧枚举值
_OLD_TASK_STATUS = ("pending", "cloning", "parsing", "embedding", "generating",
                    "completed", "failed", "cancelled")
_NEW_TASK_STATUS = _OLD_TASK_STATUS + ("interrupted",)

_OLD_REPO_STATUS = ("pending", "cloning", "ready", "error", "syncing")
_NEW_REPO_STATUS = _OLD_REPO_STATUS + ("interrupted",)


def upgrade() -> None:
    # SQLite 不支持 ALTER COLUMN，使用 batch_alter_table 重建表
    with op.batch_alter_table("tasks", recreate="always") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.Enum(*_OLD_TASK_STATUS, name="taskstatus"),
            type_=sa.Enum(*_NEW_TASK_STATUS, name="taskstatus"),
            existing_nullable=False,
        )

    with op.batch_alter_table("repositories", recreate="always") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.Enum(*_OLD_REPO_STATUS, name="repostatus"),
            type_=sa.Enum(*_NEW_REPO_STATUS, name="repostatus"),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("tasks", recreate="always") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.Enum(*_NEW_TASK_STATUS, name="taskstatus"),
            type_=sa.Enum(*_OLD_TASK_STATUS, name="taskstatus"),
            existing_nullable=False,
        )

    with op.batch_alter_table("repositories", recreate="always") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.Enum(*_NEW_REPO_STATUS, name="repostatus"),
            type_=sa.Enum(*_OLD_REPO_STATUS, name="repostatus"),
            existing_nullable=False,
        )
