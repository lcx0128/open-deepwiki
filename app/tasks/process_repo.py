import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from app.celery_app import celery_app
from app.config import settings

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def process_repository_task(
    self,
    task_id: str,
    repo_id: str,
    repo_url: str,
    pat_token: str = None,
    branch: str = None,  # None = 使用远端默认分支
    llm_provider: str = None,
    llm_model: str = None,
):
    """
    仓库处理主任务 (Celery Task)。
    注意：Celery Task 是同步函数，内部通过 asyncio.run() 调用异步代码。
    失败时自动重试最多 2 次（30s → 60s 指数退避），重试前重置任务状态为 PENDING。
    """
    try:
        asyncio.run(_run_task(
            task_id=task_id,
            repo_id=repo_id,
            repo_url=repo_url,
            pat_token=pat_token,
            branch=branch,
            llm_provider=llm_provider,
            llm_model=llm_model,
        ))
    except Exception as exc:
        if self.request.retries < self.max_retries:
            countdown = 30 * (2 ** self.request.retries)  # 30s → 60s
            logger.warning(
                f"[Task] 任务异常，{countdown}s 后自动重试 "
                f"(第 {self.request.retries + 1}/{self.max_retries} 次): {exc}"
            )
            try:
                asyncio.run(_reset_task_for_retry(task_id))
            except Exception as reset_err:
                logger.warning(f"[Task] 重置任务状态失败，仍将重试: {reset_err}")
            raise self.retry(exc=exc, countdown=countdown)
        raise


async def _run_task(
    task_id: str,
    repo_id: str,
    repo_url: str,
    pat_token: str = None,
    branch: str = None,  # None = 使用远端默认分支
    llm_provider: str = None,
    llm_model: str = None,
):
    """异步任务执行主体"""
    from app.database import async_session_factory
    from app.models.task import Task, TaskStatus, TaskType
    from app.models.repository import Repository, RepoStatus
    from app.tasks.git_operations import clone_repository
    # Celery worker 是独立进程，不经过 FastAPI lifespan，需自行初始化 Redis
    from app.core.redis_client import init_redis, close_redis

    logger.info(f"[Task] 开始执行: task_id={task_id} repo_id={repo_id} url={repo_url} branch={branch}")

    # init_redis 单独保护：失败时尝试将 DB 状态标记为 FAILED
    try:
        await init_redis()
        logger.info("[Task] Redis 初始化成功")
    except Exception as e:
        logger.error(f"[Task] Redis 初始化失败，任务无法执行: {e}", exc_info=True)
        try:
            async with async_session_factory() as db:
                await _fail_task(db, task_id, f"Redis 初始化失败: {e}", stage="cloning")
        except Exception as db_err:
            logger.error(f"[Task] 标记任务失败时 DB 也出错: {db_err}")
        raise

    try:
        async with async_session_factory() as db:
            try:
                _stage = "cloning"  # 追踪当前阶段，用于异常时记录 failed_at_stage

                # ===== WIKI_REGENERATE 快速路径：跳过阶段1-3，直接进行Wiki生成 =====
                task_record_pre = await db.get(Task, task_id)
                if task_record_pre and task_record_pre.type == TaskType.WIKI_REGENERATE:
                    _stage = "generating"
                    await _update_task(db, task_id, TaskStatus.GENERATING, 75, "正在重新生成 Wiki 文档...")
                    await _publish(task_id, "generating", 75, "正在重新生成 Wiki 文档...")
                    wiki_id = None
                    try:
                        from app.services.wiki_generator import generate_wiki

                        async def _wiki_regen_progress(pct: float, msg: str):
                            await _publish(task_id, "generating", 75 + pct * 0.2, msg)

                        wiki_id = await generate_wiki(
                            db, repo_id, llm_provider, llm_model,
                            progress_callback=_wiki_regen_progress,
                        )
                    except NotImplementedError:
                        pass
                    await _update_task(db, task_id, TaskStatus.COMPLETED, 100, "Wiki 重新生成完成")
                    repo_obj = await db.get(Repository, repo_id)
                    if repo_obj:
                        repo_obj.last_synced_at = datetime.now(timezone.utc)
                    await db.commit()
                    await _publish(task_id, "completed", 100, "Wiki 重新生成完成",
                                   **({"wiki_id": wiki_id} if wiki_id else {}))
                    return

                # ===== 阶段 1: 克隆仓库 =====
                await _update_task(db, task_id, TaskStatus.CLONING, 5, "正在克隆仓库...")
                await _publish(task_id, "cloning", 5, "正在克隆仓库...")

                local_path = f"{settings.REPOS_BASE_DIR}/{repo_id}"
                # clone_repository 内部使用 subprocess，通过 to_thread 避免阻塞事件循环
                success, clone_error = await asyncio.to_thread(
                    clone_repository, repo_url, local_path, pat_token, branch
                )
                # PAT Token 在 clone_repository 内已销毁

                if not success:
                    err_detail = f"仓库克隆失败: {clone_error}" if clone_error else "仓库克隆失败"
                    await _fail_task(db, task_id, err_detail, stage="cloning")
                    await _publish(task_id, "failed", 0, err_detail)
                    return

                # 更新 Repository 记录
                repo = await db.get(Repository, repo_id)
                if repo:
                    repo.local_path = local_path
                    repo.status = RepoStatus.READY
                await db.flush()

                # 获取当前 HEAD commit hash（用于增量同步）
                import subprocess
                head_commit_hash = ""
                try:
                    result = await asyncio.to_thread(
                        subprocess.run,
                        ["git", "rev-parse", "HEAD"],
                        cwd=local_path,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if result.returncode == 0:
                        head_commit_hash = result.stdout.strip()
                except Exception as e:
                    logger.warning(f"无法获取 HEAD commit hash: {e}")

                # ===== 阶段 2: AST 解析 =====
                _stage = "parsing"
                await _update_task(db, task_id, TaskStatus.PARSING, 20, "正在解析代码结构...")
                await _publish(task_id, "parsing", 20, "正在解析代码结构...")

                # FULL_PROCESS 强制全量重建，INCREMENTAL_SYNC 只处理变更文件
                task_record = await db.get(Task, task_id)
                force_full = (task_record is not None and task_record.type == TaskType.FULL_PROCESS)

                try:
                    from app.services.parser import parse_repository
                    chunks, file_hashes = await parse_repository(
                        db, repo_id, local_path,
                        commit_hash=head_commit_hash,
                        force_full=force_full,
                    )
                except NotImplementedError:
                    # 模块二尚未实现，跳过解析阶段
                    chunks, file_hashes = [], {}

                # 严格模式：FULL_PROCESS 必须解析出 chunks，否则视为失败
                if force_full and not chunks:
                    err_detail = "解析失败：未能从仓库中提取任何代码块（chunks=0），请确认仓库包含受支持的语言文件"
                    await _fail_task(db, task_id, err_detail, stage="parsing")
                    await _publish(task_id, "failed", 0, err_detail)
                    return

                # ===== 阶段 3: 向量化 =====
                _stage = "embedding"
                await _update_task(db, task_id, TaskStatus.EMBEDDING, 50, "正在生成向量嵌入...")
                await _publish(task_id, "embedding", 50, "正在生成向量嵌入...")

                if chunks:
                    from app.services.embedder import embed_chunks

                    async def _embed_progress(pct: float, msg: str):
                        await _publish(task_id, "embedding", 50 + pct * 0.2, msg)

                    # 严格模式：向量化失败直接抛出，由外层 except 捕获并标记 FAILED
                    await embed_chunks(
                        db, repo_id, chunks,
                        file_hashes=file_hashes,
                        commit_hash=head_commit_hash,
                        progress_callback=_embed_progress,
                    )

                # ===== 阶段 4: Wiki 生成 =====
                _stage = "generating"
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
                await _fail_task(db, task_id, error_msg, stage=_stage)
                await _publish(task_id, "failed", 0, f"处理失败: {error_msg}")
                raise
    finally:
        # 确保 Redis 连接在 Celery worker 进程中被释放
        await close_redis()


async def _reset_task_for_retry(task_id: str):
    """重试前将任务状态重置为 PENDING，确保重试可从干净状态开始"""
    from app.database import async_session_factory
    from app.models.task import Task, TaskStatus
    async with async_session_factory() as db:
        task = await db.get(Task, task_id)
        if task:
            task.status = TaskStatus.PENDING
            task.error_msg = None
            task.failed_at_stage = None
        await db.commit()


async def _update_task(db, task_id: str, status, progress: float, stage: str):
    """更新数据库中的任务状态（立即 commit，确保其他连接可见）"""
    from app.models.task import Task
    task = await db.get(Task, task_id)
    if task:
        task.status = status
        task.progress_pct = progress
        task.current_stage = stage
    await db.commit()


async def _fail_task(db, task_id: str, error_msg: str, stage: str = None):
    """标记任务为失败状态，记录失败阶段"""
    from app.models.task import Task, TaskStatus
    task = await db.get(Task, task_id)
    if task:
        task.status = TaskStatus.FAILED
        task.error_msg = error_msg
        if stage:
            task.failed_at_stage = stage
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
