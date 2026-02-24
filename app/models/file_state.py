import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Integer, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database import Base


class FileState(Base):
    __tablename__ = "file_states"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    repo_id = Column(
        String(36), ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    file_path = Column(
        String(1024), nullable=False,
        comment="相对于仓库根目录的文件路径"
    )
    last_commit_hash = Column(
        String(40), nullable=False,
        comment="该文件最后一次被处理时的 commit SHA"
    )
    chunk_ids_json = Column(
        Text, nullable=False, default="[]",
        comment="JSON 数组，存储该文件在 ChromaDB 中的所有 chunk ID"
    )
    chunk_count = Column(Integer, default=0, comment="chunk 数量，用于快速统计")
    file_hash = Column(String(64), nullable=True, comment="文件内容 SHA256，用于去重")
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    # 复合唯一约束：同一仓库下文件路径唯一
    __table_args__ = (
        UniqueConstraint("repo_id", "file_path", name="uq_file_state_repo_path"),
    )

    # 关系
    repository = relationship("Repository", back_populates="file_states")
