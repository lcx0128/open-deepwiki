import uuid
import enum
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, DateTime, ForeignKey, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import relationship
from app.database import Base


class TaskType(str, enum.Enum):
    FULL_PROCESS = "full_process"
    INCREMENTAL_SYNC = "incremental_sync"
    WIKI_REGENERATE = "wiki_regenerate"
    PARSE_ONLY = "parse_only"


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    CLONING = "cloning"
    PARSING = "parsing"
    EMBEDDING = "embedding"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class Task(Base):
    __tablename__ = "tasks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    repo_id = Column(
        String(36), ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    type = Column(SAEnum(TaskType), nullable=False, default=TaskType.FULL_PROCESS)
    status = Column(SAEnum(TaskStatus), nullable=False, default=TaskStatus.PENDING)
    progress_pct = Column(Float, nullable=False, default=0.0, comment="进度百分比 0-100")
    current_stage = Column(String(64), nullable=True, comment="当前执行阶段描述")
    error_msg = Column(Text, nullable=True, comment="失败时的错误信息")
    celery_task_id = Column(String(255), nullable=True, comment="Celery 任务 ID，用于取消")
    failed_at_stage = Column(String(32), nullable=True, comment="失败时所处阶段：cloning/parsing/embedding/generating")
    files_total = Column(Integer, default=0, comment="待处理文件总数")
    files_processed = Column(Integer, default=0, comment="已处理文件数")
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    # 关系
    repository = relationship("Repository", back_populates="tasks")
