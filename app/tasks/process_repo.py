import asyncio
import json
import re
from datetime import datetime, timezone
from app.celery_app import celery_app
from app.config import settings


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def process_repository_task(
    self,
    task_id: str,
    repo_id: str,
    repo_url: str,
    pat_token: str = None,
    branch: str = "main",
    llm_provider: str = None,
    llm_model: str = None,
):
    """
    仓库处理主任务 (Celery Task)。
    注意：Celery Task 是同步函数，内部通过 asyncio.run() 调用异步代码。
    """
    asyncio.run(_run_task(
        task_id=task_id,
        repo_id=repo_id,
        repo_url=repo_url,
        pat_token=pat_token,
        branch=branch,
        llm_provider=llm_provider,
        llm_model=llm_model,
    ))


async def _run_task(
    task_id: str,
    repo_id: str,
    repo_url: str,
    pat_token: str = None,
    branch: str = "main",
    llm_provider: str = None,
    llm_model: str = None,
):
    """异步任务执行主体"""
    from app.database import async_session_factory
    from app.models.task import Task, TaskStatus
    from app.models.repository import Repository, RepoStatus
    from app.tasks.git_operations import clone_repository
    # Celery worker 是独立进程，不经过 FastAPI lifespan，需自行初始化 Redis
    from app.core.redis_client import init_redis, close_redis

    await init_redis()
    try:
        async with async_session_factory() as db:
            try:
                # ===== 阶段 1: 克隆仓库 =====
                await _update_task(db, task_id, TaskStatus.CLONING, 5, "正在克隆仓库...")
                await _publish(task_id, "cloning", 5, "正在克隆仓库...")

                local_path = f"{settings.REPOS_BASE_DIR}/{repo_id}"
                # clone_repository 内部使用 subprocess，通过 to_thread 避免阻塞事件循环
                success = await asyncio.to_thread(
                    clone_repository, repo_url, local_path, pat_token, branch
                )
                # PAT Token 在 clone_repository 内已销毁

                if not success:
                    await _fail_task(db, task_id, "仓库克隆失败")
                    await _publish(task_id, "failed", 0, "仓库克隆失败")
                    return

                # 更新 Repository 记录
                repo = await db.get(Repository, repo_id)
                if repo:
                    repo.local_path = local_path
                    repo.status = RepoStatus.READY
                await db.flush()

                # ===== 阶段 2: AST 解析 =====
                await _update_task(db, task_id, TaskStatus.PARSING, 20, "正在解析代码结构...")
                await _publish(task_id, "parsing", 20, "正在解析代码结构...")

                try:
                    from app.services.parser import parse_repository
                    chunks = await parse_repository(db, repo_id, local_path)
                except NotImplementedError:
                    # 模块二尚未实现，跳过解析阶段
                    chunks = []

                # ===== 阶段 3: 向量化 =====
                await _update_task(db, task_id, TaskStatus.EMBEDDING, 50, "正在生成向量嵌入...")
                await _publish(task_id, "embedding", 50, "正在生成向量嵌入...")

                if chunks:
                    try:
                        from app.services.embedder import embed_chunks

                        async def _embed_progress(pct: float, msg: str):
                            await _publish(task_id, "embedding", 50 + pct * 0.2, msg)

                        await embed_chunks(db, repo_id, chunks, progress_callback=_embed_progress)
                    except NotImplementedError:
                        pass

                # ===== 阶段 4: Wiki 生成 =====
                await _update_task(db, task_id, TaskStatus.GENERATING, 75, "正在生成 Wiki 文档...")
                await _publish(task_id, "generating", 75, "正在生成 Wiki 文档...")

                wiki_id = None
                try:
                    from app.services.wiki_generator import generate_wiki

                    async def _wiki_progress(pct: float, msg: str):
                        await _publish(task_id, "generating", 75 + pct * 0.2, msg)

                    wiki_id = await generate_wiki(
                        db, repo_id, llm_provider, llm_model,
                        progress_callback=_wiki_progress,
                    )
                except NotImplementedError:
                    pass

                # ===== 完成 =====
                await _update_task(db, task_id, TaskStatus.COMPLETED, 100, "处理完成")
                if repo:
                    repo.last_synced_at = datetime.now(timezone.utc)
                await db.commit()

                await _publish(
                    task_id, "completed", 100, "处理完成",
                    **({"wiki_id": wiki_id} if wiki_id else {})
                )

            except Exception as e:
                await db.rollback()
                error_msg = str(e)
                # 脱敏：移除可能残留的 Token 字符串
                error_msg = re.sub(r'https://oauth2:[^@]+@', 'https://[REDACTED]@', error_msg)
                error_msg = re.sub(r'ghp_[A-Za-z0-9_]+', '[REDACTED]', error_msg)
                error_msg = re.sub(r'glpat-[A-Za-z0-9\-_]+', '[REDACTED]', error_msg)
                await _fail_task(db, task_id, error_msg)
                await _publish(task_id, "failed", 0, f"处理失败: {error_msg}")
                raise
    finally:
        # 确保 Redis 连接在 Celery worker 进程中被释放
        await close_redis()


async def _update_task(db, task_id: str, status, progress: float, stage: str):
    """更新数据库中的任务状态"""
    from app.models.task import Task
    task = await db.get(Task, task_id)
    if task:
        task.status = status
        task.progress_pct = progress
        task.current_stage = stage
    await db.flush()


async def _fail_task(db, task_id: str, error_msg: str):
    """标记任务为失败状态"""
    from app.models.task import Task, TaskStatus
    task = await db.get(Task, task_id)
    if task:
        task.status = TaskStatus.FAILED
        task.error_msg = error_msg
    await db.commit()


async def _publish(task_id: str, status: str, progress: float, stage: str, **extra):
    """发布进度事件到 Redis"""
    try:
        from app.core.redis_client import publish_progress
        data = {
            "status": status,
            "progress_pct": round(progress, 1),
            "stage": stage,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **extra,
        }
        await publish_progress(task_id, data)
    except Exception:
        pass  # 进度发布失败不影响主任务
