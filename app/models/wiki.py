import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Integer, JSON
from sqlalchemy.orm import relationship
from app.database import Base


class Wiki(Base):
    __tablename__ = "wikis"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    repo_id = Column(
        String(36), ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    title = Column(String(512), nullable=False, comment="Wiki 标题")
    outline_json = Column(JSON, nullable=True, comment="XML 解析后的大纲结构")
    llm_provider = Column(String(64), nullable=True, comment="使用的 LLM 供应商")
    llm_model = Column(String(128), nullable=True, comment="使用的模型名称")
    created_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    sections = relationship(
        "WikiSection", back_populates="wiki",
        cascade="all, delete-orphan", order_by="WikiSection.order_index"
    )
    repository = relationship("Repository", back_populates="wikis")


class WikiSection(Base):
    __tablename__ = "wiki_sections"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    wiki_id = Column(
        String(36), ForeignKey("wikis.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    title = Column(String(512), nullable=False)
    order_index = Column(Integer, nullable=False, default=0)

    wiki = relationship("Wiki", back_populates="sections")
    pages = relationship(
        "WikiPage", back_populates="section",
        cascade="all, delete-orphan", order_by="WikiPage.order_index"
    )


class WikiPage(Base):
    __tablename__ = "wiki_pages"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    section_id = Column(
        String(36), ForeignKey("wiki_sections.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    title = Column(String(512), nullable=False)
    importance = Column(String(16), default="medium", comment="high/medium/low")
    content_md = Column(Text, nullable=True, comment="生成的 Markdown 内容")
    relevant_files = Column(JSON, nullable=True, comment="关联的源文件路径列表")
    order_index = Column(Integer, nullable=False, default=0)
    summary = Column(Text, nullable=True, comment="页面摘要（2-3句话），用于快速上手导航页生成")
    page_type = Column(String(32), nullable=True, comment="页面类型：None=普通页, quick_start_overview=项目概览, quick_start_navigation=内容导航")

    section = relationship("WikiSection", back_populates="pages")
