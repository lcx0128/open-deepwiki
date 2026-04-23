# app/schemas/mcp_types.py
from typing import List, Optional

from pydantic import BaseModel, Field


class CodeGuideline(BaseModel):
    """代码导引：Stage 1 检索结果的轻量摘要"""
    chunk_id: str
    name: str
    file_path: str
    node_type: str
    start_line: int
    end_line: int
    description: str
    relevance_score: float
    source: str = "stage1"


class FileContext(BaseModel):
    """文件上下文：Stage 2 按需获取的完整代码片段"""
    file_path: str
    start_line: int
    end_line: int
    content: str
    language: str


class GrepMatch(BaseModel):
    """Grep 搜索结果：精确文本匹配的位置和上下文"""

    file_path: str
    line_no: int
    line_content: str
    context_lines: List[str] = Field(default_factory=list)
