from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.mcp_types import FileContext
from app.services.two_stage_retriever import read_targeted_context


class TestReadTargetedContext:
    @pytest.mark.asyncio
    async def test_reads_symbol_window_when_metadata_found(self):
        target = SimpleNamespace(file_path="app/main.py", symbol_name="create_app")
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["chunk-1"],
            "metadatas": [{"start_line": 40, "end_line": 70, "parent_name": ""}],
        }
        fake_context = FileContext(
            file_path="app/main.py",
            start_line=30,
            end_line=100,
            content="selected content",
            language="python",
        )

        with (
            patch("app.services.two_stage_retriever.get_collection", return_value=mock_collection),
            patch("app.services.two_stage_retriever.read_file_context", return_value=fake_context) as mock_read,
        ):
            result = await read_targeted_context("repo-1", target)

        mock_read.assert_called_once_with("repo-1", "app/main.py", 30, 100)
        assert "// [TARGETED FILE] app/main.py (create_app, Lines 30-100)" in result
        assert "selected content" in result

    @pytest.mark.asyncio
    async def test_falls_back_to_file_head_when_symbol_missing(self):
        target = SimpleNamespace(file_path="app/main.py", symbol_name=None)
        fake_context = FileContext(
            file_path="app/main.py",
            start_line=1,
            end_line=300,
            content="head content",
            language="python",
        )

        with patch("app.services.two_stage_retriever.read_file_context", return_value=fake_context) as mock_read:
            result = await read_targeted_context("repo-1", target)

        mock_read.assert_called_once_with("repo-1", "app/main.py", 1, 300)
        assert result == "// [TARGETED FILE] app/main.py\nhead content"

    @pytest.mark.asyncio
    async def test_falls_back_when_symbol_lookup_fails(self):
        target = SimpleNamespace(file_path="app/main.py", symbol_name="create_app")
        fake_context = FileContext(
            file_path="app/main.py",
            start_line=1,
            end_line=300,
            content="fallback content",
            language="python",
        )

        with (
            patch("app.services.two_stage_retriever.get_collection", side_effect=RuntimeError("chroma down")),
            patch("app.services.two_stage_retriever.read_file_context", return_value=fake_context) as mock_read,
        ):
            result = await read_targeted_context("repo-1", target)

        mock_read.assert_called_once_with("repo-1", "app/main.py", 1, 300)
        assert result == "// [TARGETED FILE] app/main.py\nfallback content"
