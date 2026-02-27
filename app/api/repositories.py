from typing import Optional
from pathlib import Path
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
    IncrementalSyncRequest,
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

    # 3. 创建 Task 记录，先提交确保 Celery worker 能通过 task_id 查到记录
    task = Task(repo_id=repo.id, type=task_type)
    db.add(task)
    await db.flush()
    task_id = task.id  # 在 commit 前保存，expire_on_commit=False 下对象保持有效
    await db.commit()

    # 4. 发送 Celery 任务（PAT Token 仅作为参数传递，不持久化）
    # 必须在 commit 之后调用，避免 Celery worker 在事务提交前读取 DB 导致 task 为 None
    from app.tasks.process_repo import process_repository_task
    celery_result = process_repository_task.delay(
        task_id=task_id,
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

    # 获取各仓库最新失败任务的 failed_at_stage
    repo_ids = [r.id for r in repos]
    failed_at_stage_map: dict[str, Optional[str]] = {}
    if repo_ids:
        # 查询每个仓库最新的 FAILED 任务（按 created_at 倒序，取第一条）
        failed_tasks_result = await db.execute(
            select(Task.repo_id, Task.failed_at_stage)
            .where(
                Task.repo_id.in_(repo_ids),
                Task.status == TaskStatus.FAILED,
            )
            .order_by(Task.created_at.desc())
        )
        for row in failed_tasks_result.all():
            if row.repo_id not in failed_at_stage_map:
                failed_at_stage_map[row.repo_id] = row.failed_at_stage

    def _build_list_item(r) -> RepositoryListItem:
        item = RepositoryListItem.model_validate(r)
        item.failed_at_stage = failed_at_stage_map.get(r.id)
        return item

    return RepositoryListResponse(
        items=[_build_list_item(r) for r in repos],
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
    task_id = task.id
    await db.commit()

    from app.tasks.process_repo import process_repository_task
    celery_result = process_repository_task.delay(
        task_id=task_id,
        repo_id=repo_id,
        repo_url=repo.url,
        llm_provider=request.llm_provider,
        llm_model=request.llm_model,
    )
    task.celery_task_id = celery_result.id
    await db.commit()

    return RepositoryCreateResponse(
        task_id=task_id,
        repo_id=repo_id,
        status="pending",
        message="全量重新处理任务已提交",
    )


@router.post(
    "/{repo_id}/sync",
    response_model=RepositoryCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="触发增量同步",
)
async def sync_repository(
    repo_id: str,
    request: IncrementalSyncRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    对已存在的仓库触发 INCREMENTAL_SYNC 任务（git pull + 只处理变更文件 + 重新生成 Wiki）。
    要求仓库已克隆到本地（local_path 非空），否则返回 400。
    """
    repo = await db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="仓库不存在")

    if not repo.local_path:
        raise HTTPException(
            status_code=400,
            detail="仓库尚未克隆，请先提交完整处理任务",
        )

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

    task = Task(repo_id=repo_id, type=TaskType.INCREMENTAL_SYNC)
    db.add(task)
    repo.status = RepoStatus.SYNCING
    await db.flush()
    task_id = task.id
    await db.commit()

    from app.tasks.process_repo import process_repository_task
    celery_result = process_repository_task.delay(
        task_id=task_id,
        repo_id=repo_id,
        repo_url=repo.url,
        llm_provider=request.llm_provider,
        llm_model=request.llm_model,
    )
    task.celery_task_id = celery_result.id
    await db.commit()

    return RepositoryCreateResponse(
        task_id=task_id,
        repo_id=repo_id,
        status="pending",
        message="增量同步任务已提交",
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

    # 1. 撤销所有活跃 Celery 任务
    # 优先通过 Redis 标志通知 worker 取消（无需 DB 写权限，避免写锁竞争）
    active_result = await db.execute(
        select(Task).where(
            Task.repo_id == repo_id,
            ~Task.status.in_([TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]),
        )
    )
    active_tasks = active_result.scalars().all()
    if active_tasks:
        from app.core.redis_client import set_cancel_flag
        for task in active_tasks:
            # 设置 Redis 取消标志（立即生效，worker 下次检查时会感知）
            try:
                await set_cancel_flag(task.id)
            except Exception:
                pass
            if task.celery_task_id:
                try:
                    from app.celery_app import celery_app
                    celery_app.control.revoke(task.celery_task_id, terminate=False)
                except Exception:
                    pass
        # 尝试将 DB 状态设为 CANCELLED（可能因 worker 持有写锁而失败，Redis 标志已足够）
        try:
            for task in active_tasks:
                task.status = TaskStatus.CANCELLED
            await db.commit()
        except Exception:
            await db.rollback()
        # 等待 worker 检测到取消标志并在下次页面提交时释放写锁
        import asyncio as _asyncio
        await _asyncio.sleep(2.0)

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


EXT_LANG = {
    '.py': 'python', '.ts': 'typescript', '.tsx': 'tsx',
    '.js': 'javascript', '.jsx': 'jsx', '.vue': 'vue',
    '.go': 'go', '.rs': 'rust', '.java': 'java',
    '.cpp': 'cpp', '.c': 'c', '.cs': 'csharp',
    '.rb': 'ruby', '.php': 'php', '.swift': 'swift',
    '.kt': 'kotlin', '.md': 'markdown', '.json': 'json',
    '.yaml': 'yaml', '.yml': 'yaml', '.toml': 'toml',
    '.sh': 'bash', '.html': 'html', '.css': 'css',
    '.sql': 'sql', '.xml': 'xml',
}


@router.get(
    "/{repo_id}/file",
    summary="读取仓库内文件内容",
)
async def read_repository_file(
    repo_id: str,
    path: str = Query(..., description="相对于仓库根目录的文件路径，如 src/main.py"),
    start_line: Optional[int] = Query(None, ge=1, description="起始行（1-indexed，含）"),
    end_line: Optional[int] = Query(None, ge=1, description="结束行（1-indexed，含）"),
    db: AsyncSession = Depends(get_db),
):
    """
    读取已克隆仓库中指定文件的内容，支持按行范围切片。
    防止路径穿越攻击：文件路径必须位于仓库根目录内。
    """
    repo = await db.get(Repository, repo_id)
    if not repo or not repo.local_path:
        raise HTTPException(status_code=404, detail="仓库不存在或尚未克隆")

    repo_root = Path(repo.local_path).resolve()
    target = (repo_root / path).resolve()

    # 防止路径穿越
    try:
        target.relative_to(repo_root)
    except ValueError:
        raise HTTPException(status_code=403, detail="禁止访问仓库目录之外的路径")

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"文件不存在: {path}")

    try:
        content = target.read_text(encoding='utf-8', errors='replace')
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"读取文件失败: {e}")

    lines = content.splitlines(keepends=True)
    total_lines = len(lines)

    actual_start = start_line if start_line is not None else 1
    if end_line is not None:
        sliced = lines[actual_start - 1:end_line]
    else:
        sliced = lines[actual_start - 1:]

    sliced_content = ''.join(sliced)
    language = EXT_LANG.get(target.suffix.lower(), 'text')

    return {
        "file_path": path,
        "content": sliced_content,
        "start_line": actual_start,
        "total_lines": total_lines,
        "language": language,
    }
