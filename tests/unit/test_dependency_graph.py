"""Unit tests for dependency graph ChromaDB query helpers."""

from unittest.mock import MagicMock, patch

import pytest

from app.services.dependency_graph import get_callees, get_callers


class TestGetCallees:
    @pytest.mark.asyncio
    async def test_returns_callees_from_metadata(self):
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["chunk-1"],
            "metadatas": [
                {
                    "name": "handle_chat",
                    "calls": "stage1_discovery, stage2_assembly, fuse_query",
                }
            ],
        }

        with patch("app.services.dependency_graph.get_collection", return_value=mock_collection):
            result = await get_callees("repo-1", "handle_chat", "app/services/chat_service.py")

        assert result == ["stage1_discovery", "stage2_assembly", "fuse_query"]

    @pytest.mark.asyncio
    async def test_empty_file_path_returns_empty(self):
        result = await get_callees("repo-1", "handle_chat", None)

        assert result == []

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self):
        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": [], "metadatas": []}

        with patch("app.services.dependency_graph.get_collection", return_value=mock_collection):
            result = await get_callees("repo-1", "missing", "app/missing.py")

        assert result == []


class TestGetCallers:
    @pytest.mark.asyncio
    async def test_finds_callers_from_all_chunks(self):
        mock_collection = MagicMock()
        mock_collection.count.return_value = 3
        mock_collection.get.side_effect = [
            {"ids": ["target-chunk"], "metadatas": [{"name": "stage1_discovery"}]},
            {
                "ids": ["caller-1", "caller-2", "other"],
                "metadatas": [
                    {"name": "handle_chat", "calls": "stage1_discovery, apply_budget"},
                    {"name": "handle_stream", "calls": "fuse_query, stage1_discovery"},
                    {"name": "unrelated", "calls": "foo, bar"},
                ],
            },
        ]

        with patch("app.services.dependency_graph.get_collection", return_value=mock_collection):
            result = await get_callers("repo-1", "stage1_discovery", "app/services/retriever.py")

        assert result == ["handle_chat", "handle_stream"]

    @pytest.mark.asyncio
    async def test_empty_file_path_returns_empty(self):
        result = await get_callers("repo-1", "stage1_discovery", None)

        assert result == []

    @pytest.mark.asyncio
    async def test_large_repo_skips_full_scan(self):
        mock_collection = MagicMock()
        mock_collection.count.return_value = 5001
        mock_collection.get.return_value = {
            "ids": ["target-chunk"],
            "metadatas": [{"name": "stage1_discovery"}],
        }

        with patch("app.services.dependency_graph.get_collection", return_value=mock_collection):
            result = await get_callers("repo-1", "stage1_discovery", "app/services/retriever.py")

        assert result == []
        assert mock_collection.get.call_count == 1
