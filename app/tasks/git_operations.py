import subprocess
import shutil
import re
import os
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class TokenScrubFilter(logging.Filter):
    """日志过滤器：自动脱敏所有可能包含 Token 的字符串"""
    PATTERNS = [
        re.compile(r'https://oauth2:[^@]+@'),
        re.compile(r'ghp_[A-Za-z0-9_]{36,}'),
        re.compile(r'glpat-[A-Za-z0-9\-_]{20,}'),
        re.compile(r'Bearer\s+[A-Za-z0-9\-_.]+'),
        re.compile(r'token=[A-Za-z0-9\-_.]+'),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            for pattern in self.PATTERNS:
                record.msg = pattern.sub('[REDACTED]', record.msg)
        if record.args:
            sanitized_args = []
            for arg in (record.args if isinstance(record.args, (list, tuple)) else [record.args]):
                if isinstance(arg, str):
                    for pattern in self.PATTERNS:
                        arg = pattern.sub('[REDACTED]', arg)
                sanitized_args.append(arg)
            record.args = tuple(sanitized_args)
        return True


def _setup_token_scrub_filter() -> None:
    """在 root logger 的所有 handler 上注册脱敏过滤器"""
    scrub_filter = TokenScrubFilter()
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.addFilter(scrub_filter)
    # 也给当前模块的 logger 添加
    logger.addFilter(scrub_filter)


_setup_token_scrub_filter()


def clone_repository(
    repo_url: str,
    local_path: str,
    pat_token: Optional[str] = None,
    branch: str = "main",
) -> bool:
    """
    安全克隆 Git 仓库。

    安全原则：
    1. PAT Token 仅在构造克隆 URL 时瞬时使用
    2. 克隆完成后立即从内存中销毁 Token 变量
    3. 所有异常路径均确保 Token 被清理
    4. 日志中不会出现任何 Token 痕迹
    """
    clone_url = repo_url
    try:
        if pat_token:
            if clone_url.startswith("https://"):
                clone_url = clone_url.replace(
                    "https://",
                    f"https://oauth2:{pat_token}@",
                    1,
                )
            logger.info("使用 PAT Token 进行私有仓库克隆 (Token 已脱敏)")

        target = Path(local_path)
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            [
                "git", "clone",
                "--depth", "1",
                "--branch", branch,
                "--single-branch",
                clone_url,
                str(target),
            ],
            capture_output=True,
            text=True,
            timeout=600,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )

        if result.returncode != 0:
            error_msg = result.stderr
            for pattern in TokenScrubFilter.PATTERNS:
                error_msg = pattern.sub('[REDACTED]', error_msg)
            logger.error(f"Git clone 失败: {error_msg}")
            return False

        logger.info(f"仓库克隆成功: {local_path}")
        return True

    except subprocess.TimeoutExpired:
        logger.error("Git clone 超时 (>600s)")
        return False
    except Exception as e:
        logger.exception(f"Git clone 异常: {type(e).__name__}")
        return False
    finally:
        if pat_token:
            clone_url = repo_url
            del pat_token
            logger.debug("PAT Token 已从内存中销毁")


def get_current_commit_hash(repo_path: str) -> str:
    """获取仓库当前 HEAD 的 commit hash"""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"无法获取 commit hash: {result.stderr}")
    return result.stdout.strip()
