"""
Gemini 适配器说明：
使用 Gemini 的 OpenAI-compatible 端点（无需安装 google-generativeai 包）。
endpoint: https://generativelanguage.googleapis.com/v1beta/openai/
需要 GOOGLE_API_KEY 作为 Bearer Token。
"""
import asyncio
import logging
from typing import AsyncIterator, List, Optional

from openai import AsyncOpenAI, RateLimitError, APIConnectionError, InternalServerError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.services.llm.adapter import BaseLLMAdapter
from app.schemas.llm import LLMMessage, LLMResponse

logger = logging.getLogger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class GeminiAdapter(BaseLLMAdapter):
    """
    Google Gemini 适配器，使用 OpenAI-compatible endpoint。
    支持模型：gemini-1.5-pro, gemini-1.5-flash, gemini-2.0-flash 等。
    """

    def __init__(self, api_key: str, base_url: Optional[str] = None, **kwargs):
        effective_url = base_url or GEMINI_BASE_URL
        super().__init__(api_key, effective_url, **kwargs)
        self.client = AsyncOpenAI(
            api_key=api_key or "dummy-key",
            base_url=self.base_url,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type((RateLimitError, TimeoutError, ConnectionError, asyncio.TimeoutError, APIConnectionError, InternalServerError)),
        before_sleep=lambda state: logger.warning(
            f"[GeminiAdapter] 重试第 {state.attempt_number} 次"
        ),
    )
    async def generate(
        self,
        messages: List[LLMMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        response = await self._call_with_timeout(
            self.client.chat.completions.create(
                model=model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                temperature=temperature,
                **({"max_tokens": max_tokens} if max_tokens else {}),
            )
        )
        choice = response.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            model=response.model,
            usage=dict(response.usage) if response.usage else None,
            finish_reason=choice.finish_reason,
        )

    async def stream(
        self,
        messages: List[LLMMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        response = await self.client.chat.completions.create(
            model=model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=temperature,
            **({"max_tokens": max_tokens} if max_tokens else {}),
            stream=True,
        )
        async for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
