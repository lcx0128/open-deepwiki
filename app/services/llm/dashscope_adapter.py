"""
DashScope 适配器关键约束：
1. 只支持 /chat/completions 端点，绝对不能调用 /responses 端点
2. 使用 OpenAI SDK 兼容模式
3. temperature 最大为 1.0
4. base_url 默认为 https://dashscope.aliyuncs.com/compatible-mode/v1
"""
import asyncio
import logging
from typing import AsyncIterator, List, Optional

from openai import AsyncOpenAI, RateLimitError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.services.llm.adapter import BaseLLMAdapter
from app.schemas.llm import LLMMessage, LLMResponse

logger = logging.getLogger(__name__)

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class DashScopeAdapter(BaseLLMAdapter):
    """
    阿里云百炼/DashScope 适配器。
    使用 OpenAI 兼容模式，通过 openai SDK 调用。
    关键约束：只使用 /chat/completions，不使用 /responses。
    """

    def __init__(self, api_key: str, base_url: Optional[str] = None, **kwargs):
        effective_url = base_url or DASHSCOPE_BASE_URL
        super().__init__(api_key, effective_url, **kwargs)
        self.client = AsyncOpenAI(
            api_key=api_key or "dummy-key",
            base_url=self.base_url,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type((RateLimitError, TimeoutError, ConnectionError, asyncio.TimeoutError)),
        before_sleep=lambda state: logger.warning(
            f"[DashScopeAdapter] 重试第 {state.attempt_number} 次"
        ),
    )
    async def generate(
        self,
        messages: List[LLMMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        # DashScope 部分模型不支持 temperature > 1.0
        temperature = min(temperature, 1.0)
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
        temperature = min(temperature, 1.0)
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
