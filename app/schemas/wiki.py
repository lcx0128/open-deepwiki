from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class WikiPageResponse(BaseModel):
    id: str
    title: str
    importance: str
    content_md: Optional[str] = None
    relevant_files: Optional[List[str]] = None
    order_index: int

    model_config = {"from_attributes": True}


class WikiSectionResponse(BaseModel):
    id: str
    title: str
    order_index: int
    pages: List[WikiPageResponse] = []

    model_config = {"from_attributes": True}


class WikiResponse(BaseModel):
    id: str
    repo_id: str
    title: str
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    created_at: datetime
    sections: List[WikiSectionResponse] = []

    model_config = {"from_attributes": True}


class WikiRegenerateRequest(BaseModel):
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    pages: Optional[List[str]] = None  # 可选：仅重新生成指定页面 ID 列表


class WikiRegenerateResponse(BaseModel):
    task_id: str
    message: str = "Wiki 重新生成任务已提交"
