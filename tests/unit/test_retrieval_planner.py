"""
Unit tests for app/services/retrieval_planner.py
验证 PlannedTarget 返回格式和新旧 JSON 格式兼容性。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.retrieval_planner import PlannedTarget, is_broad_query, plan_retrieval


class TestPlannedTargetParsing:
    @pytest.mark.asyncio
    async def test_new_format_returns_planned_targets(self):
        """LLM 返回新格式（对象数组）时解析为 PlannedTarget 列表。"""
        mock_response = MagicMock()
        mock_response.content = '[{"file": "app/main.py", "symbol": "app"}]'

        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(return_value=mock_response)

        with patch("app.services.retrieval_planner.create_adapter", return_value=mock_adapter):
            result = await plan_retrieval("question", "index text")

        assert len(result) == 1
        assert isinstance(result[0], PlannedTarget)
        assert result[0].file_path == "app/main.py"
        assert result[0].symbol_name == "app"

    @pytest.mark.asyncio
    async def test_old_format_backward_compatible(self):
        """LLM 返回旧格式（字符串数组）时仍然正确解析。"""
        mock_response = MagicMock()
        mock_response.content = '["app/main.py", "app/config.py"]'

        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(return_value=mock_response)

        with patch("app.services.retrieval_planner.create_adapter", return_value=mock_adapter):
            result = await plan_retrieval("question", "index text")

        assert len(result) == 2
        assert result[0].file_path == "app/main.py"
        assert result[0].symbol_name is None

    @pytest.mark.asyncio
    async def test_symbol_null_parsed_as_none(self):
        """symbol 为 null 时解析为 None。"""
        mock_response = MagicMock()
        mock_response.content = '[{"file": "app/main.py", "symbol": null}]'

        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(return_value=mock_response)

        with patch("app.services.retrieval_planner.create_adapter", return_value=mock_adapter):
            result = await plan_retrieval("question", "index text")

        assert result[0].symbol_name is None

    @pytest.mark.asyncio
    async def test_max_5_targets(self):
        """最多返回 5 个目标。"""
        items = [{"file": f"file_{i}.py", "symbol": None} for i in range(10)]
        mock_response = MagicMock()
        mock_response.content = str(items).replace("'", '"').replace("None", "null")

        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(return_value=mock_response)

        with patch("app.services.retrieval_planner.create_adapter", return_value=mock_adapter):
            result = await plan_retrieval("question", "index text")

        assert len(result) <= 5

    @pytest.mark.asyncio
    async def test_error_returns_empty_list(self):
        """LLM 调用失败时返回空列表。"""
        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(side_effect=RuntimeError("timeout"))

        with patch("app.services.retrieval_planner.create_adapter", return_value=mock_adapter):
            result = await plan_retrieval("question", "index text")

        assert result == []


class TestIsBroadQuery:
    def test_chinese_broad_keywords(self):
        assert is_broad_query("列出所有的 prompt 常量") is True
        assert is_broad_query("全部的路由定义在哪") is True

    def test_english_broad_keywords(self):
        assert is_broad_query("list all API endpoints") is True
        assert is_broad_query("find all constants") is True

    def test_non_broad_query(self):
        assert is_broad_query("handle_chat 怎么工作的") is False
