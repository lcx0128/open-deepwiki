"""
Unit tests for app/services/code_searcher.py.
验证路径搜索、grep 模式提取、双路径搜索和过滤规则。
"""
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.mcp_types import CodeGuideline, GrepMatch
from app.services.code_searcher import (
    _check_rg_available,
    _parse_rg_output,
    _grep_with_python,
    _grep_with_rg,
    _is_binary_file,
    _should_skip_dir,
    _tokenize_path,
    _tokenize_query,
    extract_grep_patterns,
    grep_codebase,
    search_file_paths,
)


class TestTokenizePath:
    def test_simple_path(self):
        tokens = _tokenize_path("app/services/chat_service.py")
        assert "app" in tokens
        assert "services" in tokens
        assert "chat" in tokens
        assert "service" in tokens
        assert "py" in tokens

    def test_camel_case(self):
        tokens = _tokenize_path("src/ChatService.ts")
        assert "chat" in tokens
        assert "service" in tokens

    def test_windows_path(self):
        tokens = _tokenize_path(r"app\services\chat_service.py")
        assert "app" in tokens
        assert "services" in tokens
        assert "chat" in tokens


class TestTokenizeQuery:
    def test_camel_case_extraction(self):
        tokens = _tokenize_query("ChatService 在哪里定义")
        assert "chat" in tokens
        assert "service" in tokens

    def test_snake_case_extraction(self):
        tokens = _tokenize_query("handle_chat_stream 怎么工作")
        assert "handle" in tokens
        assert "chat" in tokens
        assert "stream" in tokens

    def test_stop_words_filtered(self):
        tokens = _tokenize_query("the function for this task")
        assert "the" not in tokens
        assert "for" not in tokens
        assert "this" not in tokens


class TestSearchFilePaths:
    @pytest.mark.asyncio
    async def test_basic_match(self):
        index_data = {
            "app/services/chat_service.py": {"functions": ["handle_chat"]},
            "app/services/parser.py": {"functions": ["parse_repo"]},
            "app/config.py": {"constants": ["DEBUG"]},
        }

        result = await search_file_paths("repo-1", "chat service", index_data)

        assert result[0] == "app/services/chat_service.py"

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self):
        result = await search_file_paths("repo-1", "", {"app/main.py": {}})
        assert result == []

    @pytest.mark.asyncio
    async def test_no_index_returns_empty(self):
        result = await search_file_paths("repo-1", "something", None)
        assert result == []

    @pytest.mark.asyncio
    async def test_max_10_results(self):
        index_data = {f"app/services/file_{index}.py": {} for index in range(20)}
        result = await search_file_paths("repo-1", "file", index_data)
        assert len(result) == 10


class TestExtractGrepPatterns:
    def test_quoted_literal(self):
        patterns = extract_grep_patterns('找 "MultipleResultsFound" 在哪')
        assert "MultipleResultsFound" in patterns

    def test_camel_case_identifier(self):
        patterns = extract_grep_patterns("ChatService 类的定义")
        assert "ChatService" in patterns

    def test_snake_case_identifier(self):
        patterns = extract_grep_patterns("handle_chat_stream 函数")
        assert "handle_chat_stream" in patterns

    def test_route_path(self):
        patterns = extract_grep_patterns("路由 /api/repositories 在哪个文件")
        assert "/api/repositories" in patterns

    def test_all_caps_constant(self):
        patterns = extract_grep_patterns("BUDGET_RATIO 常量")
        assert "BUDGET_RATIO" in patterns

    def test_max_10_patterns(self):
        long_query = " ".join(f"func_{index}" for index in range(20))
        patterns = extract_grep_patterns(long_query)
        assert len(patterns) == 10


class TestGrepWithPython:
    def test_basic_search_in_temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = os.path.join(tmpdir, "test.py")
            with open(test_file, "w", encoding="utf-8") as file_obj:
                file_obj.write("line 1\nBUDGET_RATIO = 0.8\nline 3\n")

            results = _grep_with_python(tmpdir, ["BUDGET_RATIO"], context_lines=1)

        assert len(results) == 1
        assert results[0].line_no == 2
        assert results[0].line_content == "BUDGET_RATIO = 0.8"
        assert results[0].context_lines == ["line 1", "line 3"]

    def test_skip_binary_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "image.png"), "wb") as file_obj:
                file_obj.write(b"fake png content with BUDGET_RATIO")

            results = _grep_with_python(tmpdir, ["BUDGET_RATIO"])

        assert results == []

    def test_skip_excluded_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = os.path.join(tmpdir, ".git")
            os.makedirs(git_dir)
            with open(os.path.join(git_dir, "config"), "w", encoding="utf-8") as file_obj:
                file_obj.write("SEARCH_TARGET = true\n")

            results = _grep_with_python(tmpdir, ["SEARCH_TARGET"])

        assert results == []

    def test_relative_path_with_forward_slashes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sub_dir = os.path.join(tmpdir, "src", "services")
            os.makedirs(sub_dir)
            with open(os.path.join(sub_dir, "main.py"), "w", encoding="utf-8") as file_obj:
                file_obj.write("FIND_ME = True\n")

            results = _grep_with_python(tmpdir, ["FIND_ME"])

        assert len(results) == 1
        assert results[0].file_path == "src/services/main.py"
        assert "\\" not in results[0].file_path


class TestFilterHelpers:
    def test_binary_extensions_detected(self):
        assert _is_binary_file("image.png") is True
        assert _is_binary_file("data.sqlite3") is True
        assert _is_binary_file("script.py") is False
        assert _is_binary_file("config.json") is False

    def test_excluded_dirs_detected(self):
        assert _should_skip_dir(".git") is True
        assert _should_skip_dir("node_modules") is True
        assert _should_skip_dir("__pycache__") is True
        assert _should_skip_dir("app") is False
        assert _should_skip_dir("src") is False


class TestDualPathEquivalence:
    def test_python_and_rg_same_matches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "sample.py"), "w", encoding="utf-8") as file_obj:
                file_obj.write("alpha\nBUDGET_RATIO = 0.8\ngamma\nBUDGET_RATIO_MAX = 1.0\n")

            python_results = _grep_with_python(tmpdir, ["BUDGET_RATIO"], context_lines=1)
            python_hits = {(match.file_path, match.line_no) for match in python_results}

            if _check_rg_available():
                rg_results = _grep_with_rg(tmpdir, ["BUDGET_RATIO"], context_lines=1)
                rg_hits = {(match.file_path, match.line_no) for match in rg_results}
                assert rg_hits == python_hits

    def test_excluded_dirs_both_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            node_modules_dir = os.path.join(tmpdir, "node_modules")
            os.makedirs(node_modules_dir)
            with open(os.path.join(node_modules_dir, "lib.js"), "w", encoding="utf-8") as file_obj:
                file_obj.write("TARGET_SYMBOL = true\n")
            with open(os.path.join(tmpdir, "main.py"), "w", encoding="utf-8") as file_obj:
                file_obj.write("TARGET_SYMBOL = true\n")

            python_results = _grep_with_python(tmpdir, ["TARGET_SYMBOL"])
            assert len(python_results) == 1
            assert all("node_modules" not in match.file_path for match in python_results)

            if _check_rg_available():
                rg_results = _grep_with_rg(tmpdir, ["TARGET_SYMBOL"])
                assert len(rg_results) == 1
                assert all("node_modules" not in match.file_path for match in rg_results)


class TestRipgrepPath:
    def test_parse_rg_output_keeps_before_and_after_context(self):
        repo_dir = "E:/repo"
        output = "\n".join(
            [
                "E:/repo/sample.py-1-before",
                "E:/repo/sample.py:2:TARGET = True",
                "E:/repo/sample.py-3-after",
            ]
        )

        results = _parse_rg_output(output, repo_dir, context_lines=1)

        assert results == [
            GrepMatch(
                file_path="sample.py",
                line_no=2,
                line_content="TARGET = True",
                context_lines=["before", "after"],
            )
        ]

    def test_grep_with_rg_uses_fixed_strings_and_no_invalid_binary_type(self, monkeypatch):
        captured_cmds = []

        class CompletedProcess:
            def __init__(self):
                self.returncode = 0
                self.stdout = "repo/main.py:1:TARGET = True\n"

        def fake_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return CompletedProcess()

        monkeypatch.setattr("app.services.code_searcher.subprocess.run", fake_run)

        results = _grep_with_rg("repo", ["TARGET"], context_lines=1)

        assert len(results) == 1
        assert "--fixed-strings" in captured_cmds[0]
        assert "--type-not" not in captured_cmds[0]


class TestGrepCodebase:
    @pytest.mark.asyncio
    async def test_returns_empty_for_missing_repo_dir(self):
        results = await grep_codebase("repo-1", ["TARGET"], repo_dir="E:/not/exist")
        assert results == []

    @pytest.mark.asyncio
    async def test_uses_python_fallback_when_rg_unavailable(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "main.py"), "w", encoding="utf-8") as file_obj:
                file_obj.write("TARGET = True\n")

            expected = [
                GrepMatch(
                    file_path="main.py",
                    line_no=1,
                    line_content="TARGET = True",
                    context_lines=[],
                )
            ]

            monkeypatch.setattr("app.services.code_searcher._check_rg_available", lambda: False)
            monkeypatch.setattr(
                "app.services.code_searcher._grep_with_python",
                lambda *args, **kwargs: expected,
            )

            results = await grep_codebase("repo-1", ["TARGET"], repo_dir=tmpdir)

        assert results == expected

    @pytest.mark.asyncio
    async def test_uses_rg_when_available(self, monkeypatch):
        expected = [
            GrepMatch(
                file_path="main.py",
                line_no=12,
                line_content="TARGET = True",
                context_lines=["before", "after"],
            )
        ]

        monkeypatch.setattr("app.services.code_searcher._check_rg_available", lambda: True)
        monkeypatch.setattr(
            "app.services.code_searcher._grep_with_rg",
            lambda *args, **kwargs: expected,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            results = await grep_codebase("repo-1", ["TARGET"], repo_dir=tmpdir, context_lines=1)

        assert results == expected


class TestMergeRetrievalResults:
    @pytest.mark.asyncio
    async def test_dedup_by_file_path_and_chunk_id(self):
        from app.services.two_stage_retriever import merge_retrieval_results

        vector_guidelines = [
            CodeGuideline(
                chunk_id="chunk-1",
                name="func_a",
                file_path="a.py",
                node_type="function_definition",
                start_line=1,
                end_line=10,
                description="func_a",
                relevance_score=0.9,
                source="stage1",
            )
        ]
        grep_matches = [
            GrepMatch(
                file_path="a.py",
                line_no=5,
                line_content="func_a()",
                context_lines=[],
            )
        ]

        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["chunk-1"],
            "metadatas": [{
                "name": "func_a",
                "file_path": "a.py",
                "node_type": "function_definition",
                "start_line": 1,
                "end_line": 10,
            }],
            "documents": ["def func_a():\n    pass"],
        }

        with patch("app.services.two_stage_retriever.get_collection", return_value=mock_collection):
            result = await merge_retrieval_results(vector_guidelines, [], grep_matches, "repo-1")

        assert [guideline.chunk_id for guideline in result].count("chunk-1") == 1

    @pytest.mark.asyncio
    async def test_grep_score_higher_than_path(self):
        from app.services.two_stage_retriever import merge_retrieval_results

        def fake_get(where=None, include=None, limit=None):
            if where == {"file_path": "b.py"} and limit == 5:
                return {
                    "ids": ["chunk-path"],
                    "metadatas": [{
                        "name": "func_b_path",
                        "file_path": "b.py",
                        "node_type": "function_definition",
                        "start_line": 20,
                        "end_line": 30,
                    }],
                    "documents": ["def func_b_path():\n    pass"],
                }

            return {
                "ids": ["chunk-grep"],
                "metadatas": [{
                    "name": "func_b_grep",
                    "file_path": "b.py",
                    "node_type": "function_definition",
                    "start_line": 1,
                    "end_line": 10,
                }],
                "documents": ["def func_b_grep():\n    pass"],
            }

        mock_collection = MagicMock()
        mock_collection.get.side_effect = fake_get
        grep_matches = [
            GrepMatch(
                file_path="b.py",
                line_no=5,
                line_content="func_b_grep()",
                context_lines=[],
            )
        ]

        with patch("app.services.two_stage_retriever.get_collection", return_value=mock_collection):
            result = await merge_retrieval_results([], ["b.py"], grep_matches, "repo-1")

        grep_items = [guideline for guideline in result if guideline.source == "grep"]
        path_items = [guideline for guideline in result if guideline.source == "path"]

        assert grep_items
        assert path_items
        assert grep_items[0].relevance_score > path_items[0].relevance_score

    @pytest.mark.asyncio
    async def test_empty_path_and_grep_returns_vector_only(self):
        from app.services.two_stage_retriever import merge_retrieval_results

        vector_guidelines = [
            CodeGuideline(
                chunk_id="vector-1",
                name="func_v",
                file_path="x.py",
                node_type="function_definition",
                start_line=1,
                end_line=5,
                description="",
                relevance_score=0.8,
                source="stage1",
            )
        ]

        mock_collection = MagicMock()
        with patch("app.services.two_stage_retriever.get_collection", return_value=mock_collection):
            result = await merge_retrieval_results(vector_guidelines, [], [], "repo-1")

        assert len(result) == 1
        assert result[0].source == "stage1"
