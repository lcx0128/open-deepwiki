# app/schemas/mcp_types.py
from pydantic import BaseModel
from typing import Optional


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


class FileContext(BaseModel):
    """文件上下文：Stage 2 按需获取的完整代码片段"""
    file_path: str
    start_line: int
    end_line: int
    content: str
    language: str
