import uuid
import enum
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Enum as SAEnum
from sqlalchemy.orm import relationship
from app.database import Base


class RepoStatus(str, enum.Enum):
    PENDING = "pending"
    CLONING = "cloning"
    READY = "ready"
    ERROR = "error"
    SYNCING = "syncing"


class RepoPlatform(str, enum.Enum):
    GITHUB = "github"
    GITLAB = "gitlab"
    BITBUCKET = "bitbucket"
    CUSTOM = "custom"


class Repository(Base):
    __tablename__ = "repositories"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    url = Column(String(512), nullable=False, unique=True, comment="仓库完整 URL")
    name = Column(String(255), nullable=False, comment="仓库名称，如 owner/repo")
    platform = Column(
        SAEnum(RepoPlatform), nullable=False, default=RepoPlatform.GITHUB,
        comment="代码托管平台"
    )
    default_branch = Column(String(128), default="main", comment="默认分支名称")
    local_path = Column(String(1024), nullable=True, comment="本地克隆路径")
    status = Column(
        SAEnum(RepoStatus), nullable=False, default=RepoStatus.PENDING,
        comment="仓库状态"
    )
    last_synced_at = Column(DateTime, nullable=True, comment="最后同步时间戳")
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    # 关系
    tasks = relationship("Task", back_populates="repository", cascade="all, delete-orphan")
    file_states = relationship(
        "FileState", back_populates="repository", cascade="all, delete-orphan"
    )
    wikis = relationship("Wiki", back_populates="repository", cascade="all, delete-orphan")
