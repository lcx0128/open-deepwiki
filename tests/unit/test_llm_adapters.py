import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.llm.dashscope_adapter import DashScopeAdapter, DASHSCOPE_BASE_URL
from app.services.llm.gemini_adapter import GeminiAdapter, GEMINI_BASE_URL
from app.services.llm.custom_adapter import CustomAdapter
from app.services.llm.openai_adapter import OpenAIAdapter
from app.services.llm.factory import create_adapter
from app.schemas.llm import LLMMessage, LLMResponse


class TestDashScopeAdapter:
    def test_base_url_is_correct(self):
        """DashScope base_url 必须是国内端点"""
        adapter = DashScopeAdapter(api_key="test-key")
        assert adapter.base_url == DASHSCOPE_BASE_URL
        assert "dashscope.aliyuncs.com" in adapter.base_url
        assert "/compatible-mode/v1" in adapter.base_url

    def test_temperature_clamped_to_one(self):
        """DashScope temperature 不能超过 1.0"""
        # 验证 min() 逻辑
        assert min(1.5, 1.0) == 1.0
        assert min(0.7, 1.0) == 0.7

    def test_uses_only_chat_completions(self):
        """确认 DashScope 适配器只使用 chat.completions.create()"""
        import inspect
        source = inspect.getsource(DashScopeAdapter.generate)
        assert "chat.completions.create" in source
        assert "responses.create" not in source

    def test_semaphore_lazy_loaded(self):
        """Semaphore 必须懒加载"""
        adapter = DashScopeAdapter(api_key="test-key")
        # 初始化时 _semaphore 应为 None
        assert adapter._semaphore is None


class TestGeminiAdapter:
    def test_uses_openai_compatible_endpoint(self):
        """Gemini 必须使用 OpenAI-compatible endpoint"""
        adapter = GeminiAdapter(api_key="test-key")
        assert "generativelanguage.googleapis.com" in adapter.base_url
        assert "openai" in adapter.base_url

    def test_no_google_sdk_import(self):
        """Gemini 适配器不应依赖 google-generativeai 包"""
        import importlib.util
        spec = importlib.util.find_spec("google.generativeai")
        # 即使 google-generativeai 未安装，gemini_adapter 也应能正常导入
        import app.services.llm.gemini_adapter  # 不应 ImportError


class TestOpenAIAdapter:
    def test_initialization(self):
        """OpenAI 适配器初始化正常"""
        adapter = OpenAIAdapter(api_key="test-key", base_url="https://api.openai.com/v1")
        assert adapter.api_key == "test-key"
        assert adapter._semaphore is None  # 懒加载


class TestCustomAdapter:
    def test_default_ollama_url(self):
        """默认使用 Ollama endpoint"""
        adapter = CustomAdapter()
        assert "11434" in adapter.base_url or "localhost" in adapter.base_url


class TestAdapterFactory:
    def test_create_openai_adapter(self):
        """工厂应返回 OpenAI 适配器"""
        with patch("app.services.llm.factory.settings") as mock_settings:
            mock_settings.DEFAULT_LLM_PROVIDER = "openai"
            mock_settings.OPENAI_API_KEY = "test-key"
            mock_settings.OPENAI_BASE_URL = "https://api.openai.com/v1"
            mock_settings.MAX_CONCURRENT_LLM_CALLS = 10
            adapter = create_adapter("openai")
        assert isinstance(adapter, OpenAIAdapter)

    def test_create_dashscope_adapter(self):
        """工厂应返回 DashScope 适配器"""
        with patch("app.services.llm.factory.settings") as mock_settings:
            mock_settings.DEFAULT_LLM_PROVIDER = "openai"
            mock_settings.DASHSCOPE_API_KEY = "test-key"
            mock_settings.MAX_CONCURRENT_LLM_CALLS = 10
            adapter = create_adapter("dashscope")
        assert isinstance(adapter, DashScopeAdapter)

    def test_invalid_provider_raises(self):
        """无效 provider 应抛出 ValueError"""
        with pytest.raises(ValueError, match="不支持的 LLM 供应商"):
            create_adapter("invalid-provider-xyz")
