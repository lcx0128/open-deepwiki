from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field


class ReprocessRequest(BaseModel):
    llm_provider: Optional[str] = Field(None, description="LLM 供应商标识")
    llm_model: Optional[str] = Field(None, description="模型名称")


class RepositoryCreateRequest(BaseModel):
    url: str = Field(..., description="Git 仓库完整 URL", examples=["https://github.com/owner/repo"])
    pat_token: Optional[str] = Field(None, description="私有仓库 PAT Token，用后即毁")
    branch: Optional[str] = Field(None, description="目标分支，不填则使用远端默认分支")
    llm_provider: Optional[str] = Field(None, description="LLM 供应商标识")
    llm_model: Optional[str] = Field(None, description="模型名称")


class RepositoryCreateResponse(BaseModel):
    task_id: str = Field(..., description="任务唯一标识")
    repo_id: str = Field(..., description="仓库唯一标识")
    status: str = Field("pending", description="初始任务状态")
    message: str = Field("任务已提交", description="提示信息")


class RepositoryListItem(BaseModel):
    id: str
    url: str
    name: str
    platform: str
    status: str
    last_synced_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class RepositoryListResponse(BaseModel):
    items: List[RepositoryListItem]
    total: int
    page: int
    per_page: int


class TaskStatusResponse(BaseModel):
    id: str
    repo_id: str
    type: str
    status: str
    progress_pct: float
    current_stage: Optional[str] = None
    failed_at_stage: Optional[str] = None
    files_total: int
    files_processed: int
    error_msg: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
