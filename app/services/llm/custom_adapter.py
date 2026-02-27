"""
自定义/本地模型适配器。
支持任何兼容 OpenAI /chat/completions 接口的服务：
- Ollama (http://localhost:11434/v1)
- vLLM, LM Studio, LocalAI 等
"""
import asyncio
import logging
from typing import AsyncIterator, List, Optional

from openai import AsyncOpenAI

from app.services.llm.adapter import BaseLLMAdapter
from app.schemas.llm import LLMMessage, LLMResponse

logger = logging.getLogger(__name__)


class CustomAdapter(BaseLLMAdapter):
    def __init__(
        self,
        api_key: str = "not-needed",
        base_url: str = "http://localhost:11434/v1",
        **kwargs,
    ):
        super().__init__(api_key, base_url, **kwargs)
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

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
