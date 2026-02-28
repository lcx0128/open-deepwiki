from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from app.database import Base


class RepoIndex(Base):
    __tablename__ = "repo_indexes"

    repo_id = Column(
        String(36), ForeignKey("repositories.id", ondelete="CASCADE"),
        primary_key=True, nullable=False
    )
    index_json = Column(JSON, nullable=True, comment="代码库索引: {file_path: {language, functions, classes, constants}}")
    generated_at = Column(DateTime, nullable=True, default=lambda: datetime.now(timezone.utc))

    repository = relationship("Repository", back_populates="repo_index")
