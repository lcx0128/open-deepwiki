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
    ReprocessRequest,
)
from app.utils.url_parser import parse_repo_url
import logging

logger = logging.getLogger(__name__)

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
        active_task = active_task_result.scalars().first()
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


@router.post(
    "/{repo_id}/reprocess",
    response_model=RepositoryCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="强制全量重新处理仓库",
)
async def reprocess_repository(
    repo_id: str,
    request: ReprocessRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    对已存在的仓库强制触发 FULL_PROCESS 任务（全量重新克隆、解析、向量化、生成 Wiki）。
    适用于之前任意阶段失败后需要完整重跑的场景，无需删除仓库重建。
    """
    repo = await db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="仓库不存在")

    # 检查是否有进行中的任务
    active_result = await db.execute(
        select(Task).where(
            Task.repo_id == repo_id,
            ~Task.status.in_([TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]),
        )
    )
    active_task = active_result.scalars().first()
    if active_task:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "REPO_PROCESSING",
                "detail": "该仓库已有任务在执行中",
                "existing_task_id": active_task.id,
            },
        )

    task = Task(repo_id=repo_id, type=TaskType.FULL_PROCESS)
    db.add(task)
    repo.status = RepoStatus.CLONING
    await db.flush()

    from app.tasks.process_repo import process_repository_task
    celery_result = process_repository_task.delay(
        task_id=task.id,
        repo_id=repo_id,
        repo_url=repo.url,
        llm_provider=request.llm_provider,
        llm_model=request.llm_model,
    )
    task.celery_task_id = celery_result.id
    await db.commit()

    return RepositoryCreateResponse(
        task_id=task.id,
        repo_id=repo_id,
        status="pending",
        message="全量重新处理任务已提交",
    )


@router.delete(
    "/{repo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除仓库及其所有关联数据",
)
async def delete_repository(repo_id: str, db: AsyncSession = Depends(get_db)):
    """
    删除指定仓库及其全部关联数据：
    - 撤销所有活跃 Celery 任务（解除情景1卡死状态）
    - 数据库：Repository、Task、FileState、Wiki、WikiSection、WikiPage（级联删除）
    - ChromaDB：删除该仓库的向量集合
    - 本地磁盘：删除克隆目录

    删除后可重新提交同 URL 以完整重建。
    """
    repo = await db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="仓库不存在")

    # 1. 撤销所有活跃 Celery 任务（含卡死的中间状态任务）
    active_result = await db.execute(
        select(Task).where(
            Task.repo_id == repo_id,
            ~Task.status.in_([TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]),
        )
    )
    for task in active_result.scalars().all():
        if task.celery_task_id:
            try:
                from app.celery_app import celery_app
                celery_app.control.revoke(task.celery_task_id, terminate=True)
            except Exception:
                pass  # broker 不可达时忽略，DB 级联删除仍会清理记录

    local_path = repo.local_path

    # 2. 删除 Repository（ORM cascade: Task、FileState、Wiki→Section→Page）
    await db.delete(repo)
    await db.commit()

    # 3. 删除 ChromaDB 向量集合
    try:
        from app.services.embedder import delete_collection
        delete_collection(repo_id)
    except Exception as e:
        logger.warning(f"[RepoAPI] 删除 ChromaDB 集合失败（已忽略）: {e}")

    # 4. 删除本地克隆目录（含 .git 只读文件）
    if local_path:
        import shutil
        from pathlib import Path
        from app.tasks.git_operations import _remove_readonly
        path = Path(local_path)
        if path.exists():
            try:
                shutil.rmtree(path, onerror=_remove_readonly)
                logger.info(f"[RepoAPI] 本地克隆目录已删除: {local_path}")
            except Exception as e:
                logger.warning(f"[RepoAPI] 删除本地目录失败（已忽略）: {e}")
