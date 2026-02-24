# app/schemas/chat.py
from pydantic import BaseModel
from typing import List, Optional


class ChatRequest(BaseModel):
    """对话请求体"""
    repo_id: str
    session_id: Optional[str] = None  # 首次为 None，后续传入已有 session_id
    query: str
    llm_provider: Optional[str] = None  # 不传则使用环境变量默认值
    llm_model: Optional[str] = None     # 不传则使用 gpt-4o
    deep_research: bool = False  # 是否启用 Deep Research 模式
    messages: Optional[List[dict]] = None  # Deep Research 模式下传入的完整对话历史


class ChunkRef(BaseModel):
    """代码引用"""
    file_path: str
    start_line: int
    end_line: int
    name: str


class ChatResponse(BaseModel):
    """对话响应体"""
    session_id: str
    answer: str
    chunk_refs: List[ChunkRef] = []
    usage: Optional[dict] = None


class ChatStreamEvent(BaseModel):
    """SSE 流式事件"""
    type: str  # session_id | token | chunk_refs | deep_research_continue | done | error
    session_id: Optional[str] = None
    content: Optional[str] = None
    refs: Optional[List[ChunkRef]] = None
    error: Optional[str] = None
    iteration: Optional[int] = None
    next_iteration: Optional[int] = None
