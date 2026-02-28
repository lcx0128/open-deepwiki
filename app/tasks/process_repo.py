import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from app.celery_app import celery_app
from app.config import settings

logger = logging.getLogger(__name__)


class TaskCancelledException(Exception):
    """任务被外部取消（DELETE /repositories/{id} 触发），不触发 Celery 重试"""
    pass


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
    pages: list = None,
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
            pages=pages,
        ))
    except Exception as exc:
        if self.request.retries < self.max_retries and not isinstance(exc, TaskCancelledException):
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
    pages: list = None,
):
    """异步任务执行主体"""
    from app.database import async_session_factory
    from app.models.task import Task, TaskStatus, TaskType
    from app.models.repository import Repository, RepoStatus
    from app.tasks.git_operations import clone_repository
    # Celery worker 是独立进程，不经过 FastAPI lifespan，需自行初始化 Redis
    from app.core.redis_client import init_redis, close_redis

    logger.info(f"[Task] 开始执行: task_id={task_id} repo_id={repo_id} url={repo_url} branch={branch}")

    # ===== 入口合法性校验：防止幽灵任务（已删除仓库/已中断任务自动续传）=====
    try:
        async with async_session_factory() as _db:
            _task_check = await _db.get(Task, task_id)
            if _task_check is None:
                logger.warning(
                    f"[Task] task_id={task_id} 在数据库中不存在（仓库可能已被删除），任务终止"
                )
                return
            if _task_check.status == TaskStatus.INTERRUPTED:
                logger.warning(
                    f"[Task] task_id={task_id} 状态为 INTERRUPTED，拒绝自动续传，任务终止"
                )
                return
            _repo_check = await _db.get(Repository, repo_id)
            if _repo_check is None:
                logger.warning(
                    f"[Task] repo_id={repo_id} 在数据库中不存在（仓库可能已被删除），任务终止"
                )
                return
    except Exception as _e:
        logger.error(f"[Task] 入口校验异常，任务终止: {_e}", exc_info=True)
        return

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
                    wiki_skipped = 0
                    try:
                        from app.services.wiki_generator import generate_wiki
                        async def _wiki_regen_progress(pct: float, msg: str):
                            actual_pct = 75 + pct * 0.2
                            try:
                                from app.core.redis_client import check_cancel_flag
                                if await check_cancel_flag(task_id):
                                    raise TaskCancelledException(f"任务 {task_id} 已被取消（Redis 标志）")
                            except TaskCancelledException:
                                raise
                            except Exception:
                                pass
                            await _update_task(db, task_id, TaskStatus.GENERATING, actual_pct, msg)
                            await _publish(task_id, "generating", actual_pct, msg)

                        async def _wiki_regen_cancel():
                            from app.core.redis_client import check_cancel_flag
                            if await check_cancel_flag(task_id):
                                raise TaskCancelledException(f"任务 {task_id} 已被取消（Redis 标志）")

                        # ===== 阶段 3.5: 生成代码库索引 =====
                        try:
                            from app.services.codebase_indexer import generate_codebase_index
                            await generate_codebase_index(repo_id, db)
                            logger.info(f"[Task] 代码库索引生成完成: repo_id={repo_id}")
                        except Exception as _idx_err:
                            logger.warning(f"[Task] 代码库索引生成失败（不影响主流程）: {_idx_err}")

                        if pages:
                            from app.services.wiki_generator import regenerate_specific_pages
                            wiki_result = await regenerate_specific_pages(
                                db, repo_id, pages, llm_provider, llm_model,
                                progress_callback=_wiki_regen_progress,
                                cancel_checker=_wiki_regen_cancel,
                            )
                        else:
                            wiki_result = await generate_wiki(
                                db, repo_id, llm_provider, llm_model,
                                progress_callback=_wiki_regen_progress,
                                cancel_checker=_wiki_regen_cancel,
                            )
                        wiki_id = wiki_result["wiki_id"]
                        wiki_skipped = wiki_result.get("skipped_pages", 0)
                    except NotImplementedError:
                        pass
                    await _update_task(db, task_id, TaskStatus.COMPLETED, 100, "Wiki 重新生成完成")
                    repo_obj = await db.get(Repository, repo_id)
                    if repo_obj:
                        repo_obj.last_synced_at = datetime.now(timezone.utc)
                    await db.commit()
                    await _publish(task_id, "completed", 100, "Wiki 重新生成完成",
                                   **({"wiki_id": wiki_id} if wiki_id else {}),
                                   **({"skipped_pages": wiki_skipped} if wiki_skipped else {}))
                    return

                # ===== 阶段 1: 克隆/同步仓库 =====
                task_record_stage1 = await db.get(Task, task_id)
                is_incremental = (task_record_stage1 is not None and task_record_stage1.type == TaskType.INCREMENTAL_SYNC)

                if is_incremental:
                    await _update_task(db, task_id, TaskStatus.CLONING, 5, "正在同步仓库最新代码...")
                    await _publish(task_id, "cloning", 5, "正在同步仓库最新代码...")

                    # 从 Repository 获取本地路径和分支
                    repo_check = await db.get(Repository, repo_id)
                    local_path = repo_check.local_path if repo_check else f"{settings.REPOS_BASE_DIR}/{repo_id}"
                    sync_branch = branch or (repo_check.default_branch if repo_check else "main") or "main"
                    repo = repo_check

                    # 执行 git fetch + diff + merge + 清理 ChromaDB 已删除文件
                    # 初始化 sync_stats 为安全默认值，防止 apply_incremental_sync 异常时 NameError
                    sync_stats = {"added": 0, "modified": 0, "deleted": 0, "unchanged": 0, "changed_paths": []}
                    try:
                        from app.tasks.incremental_sync import apply_incremental_sync
                        from app.services.embedder import get_collection
                        collection = get_collection(repo_id)
                        sync_stats = await apply_incremental_sync(
                            db, repo_id, local_path, collection, branch=sync_branch
                        )
                        stats_msg = (
                            f"同步完成：新增 {sync_stats['added']} / "
                            f"修改 {sync_stats['modified']} / "
                            f"删除 {sync_stats['deleted']} 个文件"
                        )
                        await _publish(task_id, "cloning", 15, stats_msg,
                                       sync_stats=sync_stats)
                    except Exception as e:
                        logger.warning(f"[Task] 增量同步 git 操作失败，继续处理: {e}")
                        local_path = f"{settings.REPOS_BASE_DIR}/{repo_id}"

                    # 获取当前 HEAD commit hash
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

                else:
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

                    # 更新 Repository 记录（仅更新本地路径，状态将在全流程完成后更新为 READY）
                    repo = await db.get(Repository, repo_id)
                    if repo:
                        repo.local_path = local_path
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

                # ===== 阶段 3.5: 生成代码库索引 =====
                try:
                    from app.services.codebase_indexer import generate_codebase_index
                    await generate_codebase_index(repo_id, db)
                    logger.info(f"[Task] 代码库索引生成完成: repo_id={repo_id}")
                except Exception as _idx_err:
                    logger.warning(f"[Task] 代码库索引生成失败（不影响主流程）: {_idx_err}")

                # ===== 阶段 4: Wiki 生成 / 增量更新 =====
                _stage = "generating"
                await _update_task(db, task_id, TaskStatus.GENERATING, 75, "正在生成 Wiki 文档...")
                await _publish(task_id, "generating", 75, "正在生成 Wiki 文档...")

                wiki_id = None
                wiki_skipped = 0
                wiki_regen_suggestion = None
                try:
                    from app.services.wiki_generator import generate_wiki, update_wiki_incrementally

                    async def _wiki_progress(pct: float, msg: str):
                        actual_pct = 75 + pct * 0.2
                        try:
                            from app.core.redis_client import check_cancel_flag
                            if await check_cancel_flag(task_id):
                                raise TaskCancelledException(f"任务 {task_id} 已被取消（Redis 标志）")
                        except TaskCancelledException:
                            raise
                        except Exception:
                            pass
                        await _update_task(db, task_id, TaskStatus.GENERATING, actual_pct, msg)
                        await _publish(task_id, "generating", actual_pct, msg)

                    async def _wiki_cancel():
                        from app.core.redis_client import check_cancel_flag
                        if await check_cancel_flag(task_id):
                            raise TaskCancelledException(f"任务 {task_id} 已被取消（Redis 标志）")

                    if is_incremental and sync_stats.get("changed_paths"):
                        # 增量同步：使用真正的增量 Wiki 更新
                        incr_result = await update_wiki_incrementally(
                            db, repo_id,
                            changed_files=sync_stats["changed_paths"],
                            llm_provider=llm_provider,
                            llm_model=llm_model,
                            progress_callback=_wiki_progress,
                            cancel_checker=_wiki_cancel,
                        )
                        wiki_id = incr_result.get("wiki_id")
                        wiki_skipped = incr_result.get("skipped_pages", 0)
                        if incr_result["status"] == "full_regen_suggested":
                            wiki_regen_suggestion = incr_result.get("suggestion_reason")
                    else:
                        # 全量处理：全量生成 Wiki
                        wiki_result = await generate_wiki(
                            db, repo_id, llm_provider, llm_model,
                            progress_callback=_wiki_progress,
                            cancel_checker=_wiki_cancel,
                        )
                        wiki_id = wiki_result["wiki_id"]
                        wiki_skipped = wiki_result.get("skipped_pages", 0)
                except NotImplementedError:
                    pass

                # ===== 完成 =====
                await _update_task(db, task_id, TaskStatus.COMPLETED, 100, "处理完成")
                if repo:
                    repo.status = RepoStatus.READY
                    repo.last_synced_at = datetime.now(timezone.utc)
                await db.commit()

                await _publish(
                    task_id, "completed", 100, "处理完成",
                    **({"wiki_id": wiki_id} if wiki_id else {}),
                    **({"wiki_regen_suggestion": wiki_regen_suggestion} if wiki_regen_suggestion else {}),
                    **({"skipped_pages": wiki_skipped} if wiki_skipped else {}),
                )

            except Exception as e:
                await db.rollback()
                # 任务被外部取消时，优雅退出，不标记为 FAILED，不重试
                if isinstance(e, TaskCancelledException):
                    logger.info(f"[Task] 任务已被取消，正常退出: task_id={task_id}")
                    return
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
        if task and task.status not in (TaskStatus.INTERRUPTED, TaskStatus.CANCELLED):
            task.status = TaskStatus.PENDING
            task.error_msg = None
            task.failed_at_stage = None
        await db.commit()


async def _update_task(db, task_id: str, status, progress: float, stage: str):
    """更新数据库中的任务状态（立即 commit，确保其他连接可见）"""
    from app.models.task import Task, TaskStatus
    task = await db.get(Task, task_id)
    if task:
        # 检查是否已被外部取消或中止（DELETE/abort 端点会先将状态设为 CANCELLED/INTERRUPTED）
        if task.status in (TaskStatus.CANCELLED, TaskStatus.INTERRUPTED):
            raise TaskCancelledException(f"任务 {task_id} 已被取消/中止，停止处理")
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
