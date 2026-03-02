"""
文件日志配置模块。

通过 .env 中的 ENABLE_FILE_LOGGING=true 开启，产生两类日志文件：
  logs/app.log          — 调试日志，镜像所有控制台输出（FastAPI + Celery）
  logs/llm_failures.log — LLM 失败详情，含完整 prompt 和响应，便于调试

日志轮转：单文件达到 LOG_MAX_SIZE_MB 上限后自动轮转，保留 LOG_BACKUP_COUNT 份。
"""
import logging
import logging.handlers
from pathlib import Path

_file_logging_initialized = False


def setup_file_logging(settings) -> None:
    """
    初始化文件日志。幂等：重复调用无副作用。

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

    # ── 1. 调试日志：镜像所有控制台输出 ─────────────────────────────────────
    app_handler = logging.handlers.RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=max_bytes,
        backupCount=settings.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    app_handler.setLevel(logging.DEBUG)
    app_handler.setFormatter(fmt)
    logging.getLogger().addHandler(app_handler)

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
        f"[Logging] 文件日志已启用 → {log_dir.resolve()} "
        f"（单文件上限 {settings.LOG_MAX_SIZE_MB} MB，保留 {settings.LOG_BACKUP_COUNT} 份）"
    )
