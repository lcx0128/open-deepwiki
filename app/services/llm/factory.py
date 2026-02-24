import logging
from app.services.llm.adapter import BaseLLMAdapter
from app.services.llm.openai_adapter import OpenAIAdapter
from app.services.llm.dashscope_adapter import DashScopeAdapter
from app.services.llm.gemini_adapter import GeminiAdapter
from app.services.llm.custom_adapter import CustomAdapter
from app.config import settings

logger = logging.getLogger(__name__)


def create_adapter(provider: str = None, model: str = None) -> BaseLLMAdapter:
    """
    适配器工厂：根据 provider 名称创建对应的 LLM 适配器实例。

    provider 优先级：参数 > 环境变量 DEFAULT_LLM_PROVIDER > "openai"
    """
    provider = (provider or settings.DEFAULT_LLM_PROVIDER or "openai").lower()
    max_concurrent = settings.MAX_CONCURRENT_LLM_CALLS

    logger.info(f"[LLMFactory] 创建适配器: provider={provider}")

    if provider == "openai":
        return OpenAIAdapter(
            api_key=settings.OPENAI_API_KEY or "dummy-key",
            base_url=settings.OPENAI_BASE_URL,
            max_concurrent=max_concurrent,
        )
    elif provider == "dashscope":
        return DashScopeAdapter(
            api_key=settings.DASHSCOPE_API_KEY or "dummy-key",
            max_concurrent=max_concurrent,
        )
    elif provider == "gemini":
        return GeminiAdapter(
            api_key=settings.GOOGLE_API_KEY or "dummy-key",
            max_concurrent=max_concurrent,
        )
    elif provider == "custom":
        return CustomAdapter(
            api_key=settings.CUSTOM_LLM_API_KEY or "not-needed",
            base_url=settings.CUSTOM_LLM_BASE_URL or "http://localhost:11434/v1",
            max_concurrent=max_concurrent,
        )
    else:
        raise ValueError(f"不支持的 LLM 供应商: {provider}，支持的值: openai/dashscope/gemini/custom")
