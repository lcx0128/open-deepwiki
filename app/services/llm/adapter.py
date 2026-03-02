import asyncio
import logging
from abc import ABC, abstractmethod
from typing import AsyncIterator, List, Optional

from app.schemas.llm import LLMMessage, LLMResponse

logger = logging.getLogger(__name__)
_llm_failure_logger = logging.getLogger("llm.failures")

# 单次 LLM API 调用超时（秒），超时后触发重试
LLM_CALL_TIMEOUT = 240  # 4 分钟


def _make_before_sleep_log(adapter_name: str):
    """
    生成 tenacity before_sleep 回调。
    429 / 限流重试时：
      - 向终端输出 WARNING 级日志，包含等待秒数和错误详情
      - 同步写入 llm_failures.log 文件（ENABLE_FILE_LOGGING=true 时生效）
    其他错误（超时、连接失败等）：仅输出 WARNING，不写文件日志。
    """
    def _before_sleep(retry_state) -> None:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        wait_secs = retry_state.upcoming_sleep
        attempt = retry_state.attempt_number

        error_type = type(exc).__name__ if exc else "Unknown"
        error_detail = str(exc)[:300] if exc else ""
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        is_rate_limit = status_code == 429 or "RateLimitError" in error_type

        if is_rate_limit:
            console_msg = (
                f"[{adapter_name}] API 频率限制 (429) — "
                f"第 {attempt} 次重试，暂停等待 {wait_secs:.0f}s 后继续\n"
                f"  错误详情: {error_detail}"
            )
            logger.warning(console_msg)
            try:
                from app.config import settings
                if settings.ENABLE_FILE_LOGGING:
                    _llm_failure_logger.warning(
                        "\n%s\n上下文: %s\n错误: %s\n%s",
                        "=" * 60,
                        f"{adapter_name} 触发429限流 | 第{attempt}次重试 | 等待{wait_secs:.0f}s",
                        error_detail,
                        "=" * 60,
                    )
            except Exception:
                pass
        else:
            logger.warning(
                f"[{adapter_name}] 请求失败 ({error_type}) — "
                f"第 {attempt} 次重试，等待 {wait_secs:.0f}s | {error_detail}"
            )

    return _before_sleep


def _retry_after_wait(retry_state) -> float:
    """
    429 重试等待策略（供各 LLM 适配器共用）：
    1. 优先读取响应头 Retry-After / x-ratelimit-reset-after
       （openai SDK 的 RateLimitError 继承 APIStatusError，携带 .response 对象）
    2. 兜底指数退避：4s → 8s → 16s → 32s → 64s → 120s（上限）
    """
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if exc is not None:
        try:
            response = getattr(exc, "response", None)
            headers = getattr(response, "headers", None)
            if headers is not None:
                raw = (
                    headers.get("retry-after")
                    or headers.get("Retry-After")
                    or headers.get("x-ratelimit-reset-after")
                )
                if raw is not None:
                    wait_secs = float(raw) + 1.0  # +1s 保险边距，避免边界竞争
                    logger.info(
                        f"[LLMAdapter] 读取到 Retry-After={float(raw):.0f}s，"
                        f"等待 {wait_secs:.0f}s 后重试"
                    )
                    return min(wait_secs, 180.0)  # 最多等 3 分钟
        except Exception:
            pass
    # 兜底：指数退避 2×2^n，上限 120s
    # 各次等待：4s → 8s → 16s → 32s → 64s → 120s
    attempt = retry_state.attempt_number
    return min(2.0 * (2.0 ** attempt), 120.0)


class BaseLLMAdapter(ABC):
    """LLM 适配器基类"""

    def __init__(self, api_key: str, base_url: Optional[str] = None,
                 max_concurrent: int = 10):
        self.api_key = api_key
        self.base_url = base_url
        self._max_concurrent = max_concurrent
        self._semaphore: Optional[asyncio.Semaphore] = None

    def _get_semaphore(self) -> asyncio.Semaphore:
        """懒加载 Semaphore，确保在当前事件循环上下文中创建"""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_concurrent)
        return self._semaphore

    async def _call_with_timeout(self, coro, timeout: float = LLM_CALL_TIMEOUT):
        """包装 API 调用，超时后抛出 asyncio.TimeoutError 以触发重试"""
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                f"[LLMAdapter] API 调用超时（{timeout:.0f}s），将触发重试"
            )
            raise

    @abstractmethod
    async def generate(
        self,
        messages: List[LLMMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """同步生成（返回完整响应）"""
        ...

    @abstractmethod
    async def stream(
        self,
        messages: List[LLMMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """流式生成（逐 token 返回）"""
        ...

    async def generate_with_rate_limit(
        self, messages: List[LLMMessage], model: str,
        temperature: float = 0.7, max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """带速率限制的生成"""
        async with self._get_semaphore():
            return await self.generate(messages, model, temperature, max_tokens)

    async def stream_with_rate_limit(
        self, messages: List[LLMMessage], model: str,
        temperature: float = 0.7, max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """带速率限制的流式生成"""
        async with self._get_semaphore():
            async for chunk in self.stream(messages, model, temperature, max_tokens):
                yield chunk

    async def aclose(self) -> None:
        """释放底层 HTTP 客户端资源，子类按需覆写"""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.aclose()
