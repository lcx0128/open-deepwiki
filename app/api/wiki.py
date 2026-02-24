import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.wiki import Wiki, WikiSection, WikiPage
from app.models.repository import Repository
from app.models.task import Task, TaskType, TaskStatus
from app.schemas.wiki import WikiResponse, WikiRegenerateRequest, WikiRegenerateResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wiki", tags=["wiki"])


@router.get(
    "/{repo_id}",
    response_model=WikiResponse,
    summary="获取 Wiki 内容",
)
async def get_wiki(repo_id: str, db: AsyncSession = Depends(get_db)):
    """
    获取指定仓库的 Wiki 文档内容（含所有章节和页面）。
    """
    # 验证仓库存在
    repo = await db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="仓库不存在")

    # 查询 Wiki，eager load sections 和 pages
    result = await db.execute(
        select(Wiki)
        .where(Wiki.repo_id == repo_id)
        .options(
            selectinload(Wiki.sections).selectinload(WikiSection.pages)
        )
        .order_by(Wiki.created_at.desc())
        .limit(1)
    )
    wiki = result.scalar_one_or_none()

    if not wiki:
        raise HTTPException(
            status_code=404,
            detail="该仓库尚未生成 Wiki，请等待处理任务完成"
        )

    return WikiResponse.model_validate(wiki)


@router.post(
    "/{repo_id}/regenerate",
    response_model=WikiRegenerateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="重新生成 Wiki",
)
async def regenerate_wiki(
    repo_id: str,
    request: WikiRegenerateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    触发 Wiki 重新生成任务。
    若指定 pages 列表，仅重新生成对应页面。
    """
    # 验证仓库存在
    repo = await db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="仓库不存在")

    # 检查是否有进行中的 Wiki 生成任务
    active_task_result = await db.execute(
        select(Task).where(
            Task.repo_id == repo_id,
            Task.type == TaskType.WIKI_REGENERATE,
            ~Task.status.in_([TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]),
        )
    )
    active_task = active_task_result.scalars().first()
    if active_task:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "WIKI_REGENERATING",
                "detail": "该仓库正在重新生成 Wiki",
                "existing_task_id": active_task.id,
            },
        )

    # 创建 Wiki 重新生成任务
    task = Task(
        repo_id=repo_id,
        type=TaskType.WIKI_REGENERATE,
        status=TaskStatus.PENDING,
    )
    db.add(task)
    await db.flush()

    # 发送 Celery 任务
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

    logger.info(f"[WikiAPI] Wiki 重新生成任务已创建: task_id={task.id} repo_id={repo_id}")
    return WikiRegenerateResponse(task_id=task.id)


@router.delete(
    "/{repo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除 Wiki 文档",
)
async def delete_wiki(repo_id: str, db: AsyncSession = Depends(get_db)):
    """
    删除指定仓库的 Wiki 文档（含所有章节和页面），保留仓库、向量数据和任务记录。
    删除后可立即调用 POST /api/wiki/{repo_id}/regenerate 重新生成。
    """
    repo = await db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="仓库不存在")

    result = await db.execute(select(Wiki).where(Wiki.repo_id == repo_id))
    wiki = result.scalar_one_or_none()
    if not wiki:
        raise HTTPException(status_code=404, detail="Wiki 不存在")

    await db.delete(wiki)  # cascade: WikiSection → WikiPage
    await db.commit()
    logger.info(f"[WikiAPI] Wiki 已删除: repo_id={repo_id} wiki_id={wiki.id}")


@router.get(
    "/{repo_id}/pages/{page_id}",
    summary="获取单个 Wiki 页面",
)
async def get_wiki_page(
    repo_id: str,
    page_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    获取指定 Wiki 页面的完整 Markdown 内容。
    """
    page = await db.get(WikiPage, page_id)
    if not page:
        raise HTTPException(status_code=404, detail="Wiki 页面不存在")

    # 验证页面属于该仓库的 Wiki
    result = await db.execute(
        select(WikiSection)
        .where(WikiSection.id == page.section_id)
        .options(selectinload(WikiSection.wiki))
    )
    section = result.scalar_one_or_none()
    if not section or section.wiki.repo_id != repo_id:
        raise HTTPException(status_code=404, detail="Wiki 页面不存在")

    return {
        "id": page.id,
        "title": page.title,
        "importance": page.importance,
        "content_md": page.content_md,
        "relevant_files": page.relevant_files,
        "order_index": page.order_index,
    }
