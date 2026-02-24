import asyncio
from abc import ABC, abstractmethod
from typing import AsyncIterator, List, Optional

from app.schemas.llm import LLMMessage, LLMResponse


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
