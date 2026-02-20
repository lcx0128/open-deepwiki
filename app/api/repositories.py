from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.database import get_db
from app.models.repository import Repository, RepoStatus
from app.models.task import Task, TaskStatus, TaskType
from app.schemas.repository import (
    RepositoryCreateRequest,
    RepositoryCreateResponse,
    RepositoryListItem,
    RepositoryListResponse,
)
from app.utils.url_parser import parse_repo_url

router = APIRouter(prefix="/api/repositories", tags=["repositories"])


@router.post(
    "",
    response_model=RepositoryCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="提交仓库处理任务",
)
async def submit_repository(
    request: RepositoryCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    接收仓库 URL，创建 Repository 和 Task 记录，推入 Celery 队列，立即返回 Task ID。
    """
    # 1. 解析并验证 URL
    repo_info = parse_repo_url(request.url)
    if not repo_info:
        raise HTTPException(
            status_code=400,
            detail={"error": "INVALID_URL", "detail": "无法解析仓库 URL"}
        )

    # 2. 检查是否已存在正在处理的任务
    existing_result = await db.execute(
        select(Repository).where(Repository.url == request.url)
    )
    existing_repo = existing_result.scalar_one_or_none()

    if existing_repo:
        # 检查是否有进行中的任务
        active_task_result = await db.execute(
            select(Task).where(
                Task.repo_id == existing_repo.id,
                ~Task.status.in_([
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                ])
            )
        )
        active_task = active_task_result.scalar_one_or_none()
        if active_task:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "REPO_PROCESSING",
                    "detail": "该仓库正在处理中",
                    "existing_task_id": active_task.id,
                }
            )
        repo = existing_repo
        task_type = TaskType.INCREMENTAL_SYNC
    else:
        # 新仓库
        repo = Repository(
            url=request.url,
            name=repo_info["name"],
            platform=repo_info["platform"],
            default_branch=request.branch or repo_info.get("default_branch", "main"),
        )
        db.add(repo)
        await db.flush()
        task_type = TaskType.FULL_PROCESS

    # 3. 创建 Task 记录
    task = Task(repo_id=repo.id, type=task_type)
    db.add(task)
    await db.flush()

    # 4. 发送 Celery 任务（PAT Token 仅作为参数传递，不持久化）
    from app.tasks.process_repo import process_repository_task
    celery_result = process_repository_task.delay(
        task_id=task.id,
        repo_id=repo.id,
        repo_url=request.url,
        pat_token=request.pat_token,
        branch=request.branch,  # None 表示使用远端默认分支
        llm_provider=request.llm_provider,
        llm_model=request.llm_model,
    )

    # 5. 回写 Celery Task ID
    task.celery_task_id = celery_result.id
    await db.commit()

    return RepositoryCreateResponse(
        task_id=task.id,
        repo_id=repo.id,
        status="pending",
        message="任务已提交，正在排队处理",
    )


@router.get(
    "",
    response_model=RepositoryListResponse,
    summary="获取仓库列表",
)
async def list_repositories(
    page: int = Query(1, ge=1, description="页码"),
    per_page: int = Query(20, ge=1, le=100, description="每页数量"),
    status: Optional[str] = Query(None, description="过滤状态"),
    db: AsyncSession = Depends(get_db),
):
    """获取仓库列表，支持分页和状态过滤。"""
    query = select(Repository)
    count_query = select(func.count(Repository.id))

    if status:
        try:
            status_enum = RepoStatus(status)
            query = query.where(Repository.status == status_enum)
            count_query = count_query.where(Repository.status == status_enum)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效状态值: {status}")

    # 获取总数
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # 分页查询
    offset = (page - 1) * per_page
    query = query.order_by(Repository.created_at.desc()).offset(offset).limit(per_page)
    result = await db.execute(query)
    repos = result.scalars().all()

    return RepositoryListResponse(
        items=[RepositoryListItem.model_validate(r) for r in repos],
        total=total,
        page=page,
        per_page=per_page,
    )
