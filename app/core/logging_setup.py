"""
文件日志配置模块。

通过 .env 中的 ENABLE_FILE_LOGGING=true 开启，产生三类日志文件：
  logs/api.log          — FastAPI 进程日志
  logs/worker.log       — Celery Worker 进程日志
  logs/llm_failures.log — LLM 失败详情，含完整 prompt 和响应，便于调试

日志轮转：单文件达到 LOG_MAX_SIZE_MB 上限后自动轮转，保留 LOG_BACKUP_COUNT 份。
"""
import logging
import logging.handlers
from pathlib import Path

_file_logging_initialized = False


def setup_file_logging(settings, process_name: str = "app") -> None:
    """
    初始化文件日志。幂等：重复调用无副作用。

    process_name 决定进程日志文件名：
      "api"    → logs/api.log    （由 main.py 传入）
      "worker" → logs/worker.log  （由 celery_app.py 传入）

    在 FastAPI lifespan 启动前和 Celery worker_init 信号中各调用一次。
    """
    global _file_logging_initialized
    if _file_logging_initialized or not settings.ENABLE_FILE_LOGGING:
        return
    _file_logging_initialized = True

    log_dir = Path(settings.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)

    max_bytes = settings.LOG_MAX_SIZE_MB * 1024 * 1024
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── 1. 进程日志：按进程类型分文件 ────────────────────────────────────────
    process_handler = logging.handlers.RotatingFileHandler(
        log_dir / f"{process_name}.log",
        maxBytes=max_bytes,
        backupCount=settings.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    process_handler.setLevel(logging.DEBUG)
    process_handler.setFormatter(fmt)
    logging.getLogger().addHandler(process_handler)

    # ── 2. LLM 失败日志：独立文件，含完整 prompt + response ─────────────────
    llm_handler = logging.handlers.RotatingFileHandler(
        log_dir / "llm_failures.log",
        maxBytes=max_bytes * 2,   # prompt 内容较大，分配双倍空间
        backupCount=settings.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    llm_handler.setLevel(logging.DEBUG)
    llm_handler.setFormatter(fmt)

    llm_logger = logging.getLogger("llm.failures")
    llm_logger.addHandler(llm_handler)
    llm_logger.setLevel(logging.DEBUG)
    llm_logger.propagate = False  # 不向 root 传播，避免 prompt 明文出现在控制台

    logging.getLogger(__name__).info(
        f"[Logging] 文件日志已启用 → {log_dir.resolve()}/{process_name}.log "
        f"（单文件上限 {settings.LOG_MAX_SIZE_MB} MB，保留 {settings.LOG_BACKUP_COUNT} 份）"
    )
