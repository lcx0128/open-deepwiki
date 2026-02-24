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

