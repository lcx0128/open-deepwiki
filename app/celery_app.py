import logging
from celery import Celery
from celery.signals import worker_ready, worker_init, task_prerun, task_postrun, task_failure
from app.config import settings

celery_app = Celery(
    "open_deepwiki",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.process_repo"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    task_time_limit=3600,
    task_soft_time_limit=3300,
    # 不接管 root logger，让我们的 basicConfig 生效
    worker_hijack_root_logger=False,
    worker_log_color=False,
)


@worker_init.connect
def setup_worker_logging(**kwargs):
    """Worker 进程启动时配置日志"""
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    logger = logging.getLogger("celery.worker")
    logger.info(f"[Worker] 日志级别: {settings.LOG_LEVEL.upper()}, Broker: {settings.REDIS_URL}")


@worker_ready.connect
def on_worker_ready(**kwargs):
    logger = logging.getLogger("celery.worker")
    logger.info("[Worker] ✅ Worker 已就绪，开始监听任务队列")
    # 标记所有未完成任务为 INTERRUPTED，禁止自动续传
    import asyncio as _asyncio
    try:
        _asyncio.run(_mark_interrupted_tasks())
    except Exception as e:
        logger.warning(f"[Worker] 标记中断任务失败（已忽略）: {e}")


async def _mark_interrupted_tasks():
    """Worker 启动时将所有非终态任务标记为 INTERRUPTED，防止幽灵任务自动续传"""
    import logging as _logging
    _logger = _logging.getLogger("celery.worker")
    try:
        from app.database import async_session_factory
        from app.models.task import Task, TaskStatus
        from app.models.repository import Repository, RepoStatus
        from sqlalchemy import select, not_

        _TERMINAL = [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.INTERRUPTED]

        async with async_session_factory() as db:
            result = await db.execute(
                select(Task).where(not_(Task.status.in_(_TERMINAL)))
            )
            tasks = result.scalars().all()
            if not tasks:
                _logger.info("[Worker] 无未完成任务，无需标记中断")
                return

            repo_ids = list({t.repo_id for t in tasks})
            for task in tasks:
                task.status = TaskStatus.INTERRUPTED
                task.error_msg = "Worker 重启，任务被中断。请点击「重新处理」恢复。"
            # 同步将对应仓库状态设为 INTERRUPTED
            repos_result = await db.execute(
                select(Repository).where(Repository.id.in_(repo_ids))
            )
            for repo in repos_result.scalars().all():
                if repo.status not in (RepoStatus.READY, RepoStatus.ERROR):
                    repo.status = RepoStatus.INTERRUPTED
            await db.commit()
            _logger.warning(
                f"[Worker] 已将 {len(tasks)} 个未完成任务标记为 INTERRUPTED，"
                f"涉及仓库: {repo_ids}"
            )
    except Exception as e:
        _logging.getLogger("celery.worker").error(f"[Worker] _mark_interrupted_tasks 异常: {e}", exc_info=True)


@task_prerun.connect
def on_task_prerun(task_id, task, args, kwargs, **kw):
    logger = logging.getLogger("celery.task")
    logger.info(f"[Task] 开始执行: {task.name} | task_id={task_id}")


@task_postrun.connect
def on_task_postrun(task_id, task, args, kwargs, retval, state, **kw):
    logger = logging.getLogger("celery.task")
    logger.info(f"[Task] 执行完毕: {task.name} | task_id={task_id} | state={state}")


@task_failure.connect
def on_task_failure(task_id, exception, traceback, **kw):
    logger = logging.getLogger("celery.task")
    logger.error(f"[Task] 执行失败: task_id={task_id} | error={exception}", exc_info=True)

