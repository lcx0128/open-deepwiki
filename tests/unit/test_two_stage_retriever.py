from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.mcp_types import CodeGuideline, FileContext
from app.services.two_stage_retriever import expand_via_dependency_graph, read_targeted_context


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


class TestExpandViaDependencyGraph:
    @pytest.mark.asyncio
    async def test_expands_callees_as_dep_graph_guidelines(self):
        guidelines = [
            CodeGuideline(
                chunk_id="caller-chunk",
                name="handle_chat",
                file_path="app/services/chat_service.py",
                node_type="function_definition",
                start_line=1,
                end_line=20,
                description="caller",
                relevance_score=0.9,
            )
        ]
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["callee-chunk"],
            "metadatas": [
                {
                    "name": "stage1_discovery",
                    "file_path": "app/services/two_stage_retriever.py",
                    "node_type": "function_definition",
                    "start_line": 10,
                    "end_line": 40,
                }
            ],
            "documents": ["async def stage1_discovery(...):"],
        }

        with (
            patch("app.services.two_stage_retriever.get_collection", return_value=mock_collection),
            patch(
                "app.services.dependency_graph.get_callees",
                AsyncMock(return_value=["stage1_discovery"]),
            ),
        ):
            result = await expand_via_dependency_graph(guidelines, "repo-1", direction="callee")

        assert len(result) == 1
        assert result[0].chunk_id == "callee-chunk"
        assert result[0].source == "dep_graph"
        assert result[0].description.startswith("[dep-graph:callee]")
        assert result[0].relevance_score == 0.65

    @pytest.mark.asyncio
    async def test_skips_existing_chunk_ids_and_caps_total_expansion(self):
        guidelines = [
            CodeGuideline(
                chunk_id="existing",
                name="root",
                file_path="root.py",
                node_type="function_definition",
                start_line=1,
                end_line=10,
                description="root",
                relevance_score=0.9,
            )
        ]
        mock_collection = MagicMock()

        def fake_get(where=None, include=None, limit=None):
            symbol = where["name"]
            return {
                "ids": ["existing", f"{symbol}-1", f"{symbol}-2"],
                "metadatas": [
                    {"name": "existing", "file_path": "root.py", "node_type": "function_definition"},
                    {"name": symbol, "file_path": f"{symbol}.py", "node_type": "function_definition"},
                    {"name": symbol, "file_path": f"{symbol}_alt.py", "node_type": "function_definition"},
                ],
                "documents": ["existing", f"def {symbol}():", f"def {symbol}_alt():"],
            }

        mock_collection.get.side_effect = fake_get

        with (
            patch("app.services.two_stage_retriever.get_collection", return_value=mock_collection),
            patch(
                "app.services.dependency_graph.get_callees",
                AsyncMock(return_value=["a", "b", "c", "d", "e", "f"]),
            ),
        ):
            result = await expand_via_dependency_graph(guidelines, "repo-1", direction="callee")

        assert "existing" not in {guideline.chunk_id for guideline in result}
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_processes_same_symbol_from_different_seed_files(self):
        guidelines = [
            CodeGuideline(
                chunk_id="seed-a",
                name="create",
                file_path="a.py",
                node_type="function_definition",
                start_line=1,
                end_line=10,
                description="create a",
                relevance_score=0.9,
            ),
            CodeGuideline(
                chunk_id="seed-b",
                name="create",
                file_path="b.py",
                node_type="function_definition",
                start_line=20,
                end_line=30,
                description="create b",
                relevance_score=0.8,
            ),
        ]
        mock_collection = MagicMock()

        def fake_get(where=None, include=None, limit=None):
            symbol = where["name"]
            return {
                "ids": [f"{symbol}-chunk"],
                "metadatas": [
                    {
                        "name": symbol,
                        "file_path": f"{symbol}.py",
                        "node_type": "function_definition",
                    }
                ],
                "documents": [f"def {symbol}():"],
            }

        mock_collection.get.side_effect = fake_get
        get_callees_mock = AsyncMock(side_effect=[["helper_a"], ["helper_b"]])

        with (
            patch("app.services.two_stage_retriever.get_collection", return_value=mock_collection),
            patch("app.services.dependency_graph.get_callees", get_callees_mock),
        ):
            result = await expand_via_dependency_graph(guidelines, "repo-1", direction="callee")

        assert get_callees_mock.await_count == 2
        assert {guideline.name for guideline in result} == {"helper_a", "helper_b"}

    @pytest.mark.asyncio
    async def test_caller_expansion_caps_seed_scans(self):
        guidelines = [
            CodeGuideline(
                chunk_id=f"seed-{index}",
                name=f"func_{index}",
                file_path=f"file_{index}.py",
                node_type="function_definition",
                start_line=1,
                end_line=10,
                description=f"func_{index}",
                relevance_score=0.9,
            )
            for index in range(20)
        ]
        mock_collection = MagicMock()
        get_callers_mock = AsyncMock(return_value=[])

        with (
            patch("app.services.two_stage_retriever.get_collection", return_value=mock_collection),
            patch("app.services.dependency_graph.get_callers", get_callers_mock),
        ):
            result = await expand_via_dependency_graph(guidelines, "repo-1", direction="caller")

        assert result == []
        assert get_callers_mock.await_count == 5
