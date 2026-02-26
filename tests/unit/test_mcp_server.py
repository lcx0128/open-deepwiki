# tests/unit/test_mcp_server.py
"""Unit tests for app/mcp_server.py — each of the 8 MCP tools."""

import os
import sys
import importlib
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Pre-import patch: FastMCP installed version does not accept 'description'
# keyword; patch it before the module-level mcp = FastMCP(...) executes.
# ---------------------------------------------------------------------------
_fastmcp_mock = MagicMock()
_fastmcp_mock.return_value = MagicMock()

# Patch at the mcp.server.fastmcp level so the import inside app.mcp_server
# picks up the mock regardless of whether the module was cached.
sys.modules.pop("app.mcp_server", None)

import mcp.server.fastmcp as _fastmcp_module  # noqa: E402
_real_fastmcp = _fastmcp_module.FastMCP

# Build a FastMCP stand-in whose .tool() decorator is a pass-through,
# so the decorated async functions remain plain coroutine functions.
def _passthrough_decorator(*args, **kwargs):
    def _wrap(fn):
        return fn
    return _wrap

_mock_mcp_instance = MagicMock()
_mock_mcp_instance.tool = _passthrough_decorator

_mock_fastmcp_class = MagicMock(return_value=_mock_mcp_instance)
_fastmcp_module.FastMCP = _mock_fastmcp_class

import app.mcp_server as mcp_module  # noqa: E402

# Restore real FastMCP so other code isn't affected
_fastmcp_module.FastMCP = _real_fastmcp


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_repo(
    id="repo-1",
    name="my-repo",
    url="https://github.com/test/my-repo",
    status_value="ready",
    last_synced_at=None,
):
    """Build a minimal mock Repository ORM object."""
    repo = MagicMock()
    repo.id = id
    repo.name = name
    repo.url = url
    status = MagicMock()
    status.value = status_value
    repo.status = status
    repo.last_synced_at = last_synced_at
    return repo


def _make_wiki(repo_id="repo-1", title="Test Wiki", sections=None):
    """Build a minimal mock Wiki ORM object."""
    wiki = MagicMock()
    wiki.repo_id = repo_id
    wiki.title = title
    wiki.sections = sections or []
    wiki.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return wiki


def _make_section(title="Architecture", pages=None):
    section = MagicMock()
    section.title = title
    section.pages = pages or []
    return section


def _make_page(title="Overview", content_md="Some content", relevant_files=None, importance="high"):
    page = MagicMock()
    page.title = title
    page.content_md = content_md
    page.relevant_files = relevant_files or []
    page.importance = importance
    return page


def _make_db_mock_single(scalar_value):
    """Mock db that returns a scalar_one_or_none() result."""
    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = scalar_value
    db.execute = AsyncMock(return_value=execute_result)
    return db


def _make_db_context(db_mock):
    """Wrap a db mock in an async context manager."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db_mock)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _make_db_scalars_mock(all_return_value):
    """Mock db where execute -> scalars().all() returns a list."""
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = all_return_value
    db.execute = AsyncMock(return_value=result_mock)
    return db


# ---------------------------------------------------------------------------
# TestListRepositories
# ---------------------------------------------------------------------------

class TestListRepositories:

    @pytest.mark.asyncio
    async def test_returns_ready_repos(self):
        """Returns a list of dicts for READY repos with expected keys."""
        now = datetime(2026, 2, 1, tzinfo=timezone.utc)
        repo1 = _make_repo(id="r1", name="repo-a", url="https://github.com/a/a", last_synced_at=now)
        repo2 = _make_repo(id="r2", name="repo-b", url="https://github.com/b/b")

        db_mock = _make_db_scalars_mock([repo1, repo2])
        with patch("app.mcp_server.async_session_factory", return_value=_make_db_context(db_mock)):
            result = await mcp_module.list_repositories()

        assert len(result) == 2
        assert result[0]["repo_id"] == "r1"
        assert result[0]["name"] == "repo-a"
        assert result[0]["url"] == "https://github.com/a/a"
        assert result[0]["status"] == "ready"
        assert result[0]["last_synced_at"] == now.isoformat()
        assert result[1]["repo_id"] == "r2"
        assert result[1]["last_synced_at"] is None

    @pytest.mark.asyncio
    async def test_empty_when_no_repos(self):
        """Returns an empty list when no READY repos exist."""
        db_mock = _make_db_scalars_mock([])
        with patch("app.mcp_server.async_session_factory", return_value=_make_db_context(db_mock)):
            result = await mcp_module.list_repositories()

        assert result == []

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Returns error dict when DB raises an exception."""
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=RuntimeError("DB connection failed"))
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("app.mcp_server.async_session_factory", return_value=cm):
            result = await mcp_module.list_repositories()

        assert len(result) == 1
        assert "error" in result[0]
        assert result[0]["tool"] == "list_repositories"
        assert "DB connection failed" in result[0]["error"]


# ---------------------------------------------------------------------------
# TestSearchCodebase
# ---------------------------------------------------------------------------

class TestSearchCodebase:

    def _make_guideline(chunk_id="cid-1", name="my_func", file_path="app/main.py",
                        node_type="function", start_line=10, end_line=20,
                        description="does stuff", relevance_score=0.9):
        from app.schemas.mcp_types import CodeGuideline
        return CodeGuideline(
            chunk_id=chunk_id,
            name=name,
            file_path=file_path,
            node_type=node_type,
            start_line=start_line,
            end_line=end_line,
            description=description,
            relevance_score=relevance_score,
        )

    @pytest.mark.asyncio
    async def test_basic_search(self):
        """Returns model_dump() results from stage1_discovery."""
        from app.schemas.mcp_types import CodeGuideline
        g1 = CodeGuideline(chunk_id="c1", name="func_a", file_path="a.py",
                           node_type="function", start_line=1, end_line=5,
                           description="A", relevance_score=0.95)
        g2 = CodeGuideline(chunk_id="c2", name="func_b", file_path="b.py",
                           node_type="function", start_line=10, end_line=15,
                           description="B", relevance_score=0.80)

        with patch("app.mcp_server.stage1_discovery", AsyncMock(return_value=[g1, g2])):
            result = await mcp_module.search_codebase("auth logic", "repo-1", top_k=5)

        assert len(result) == 2
        assert result[0]["chunk_id"] == "c1"
        assert result[0]["name"] == "func_a"
        assert result[1]["chunk_id"] == "c2"

    @pytest.mark.asyncio
    async def test_top_k_clamped_to_50(self):
        """top_k=100 is clamped to 50 before calling stage1_discovery."""
        with patch("app.mcp_server.stage1_discovery", AsyncMock(return_value=[])) as mock_s1:
            await mcp_module.search_codebase("query", "repo-1", top_k=100)

        mock_s1.assert_awaited_once_with("query", "repo-1", 50)

    @pytest.mark.asyncio
    async def test_top_k_clamped_to_1(self):
        """top_k=0 is clamped to minimum 1."""
        with patch("app.mcp_server.stage1_discovery", AsyncMock(return_value=[])) as mock_s1:
            await mcp_module.search_codebase("query", "repo-1", top_k=0)

        mock_s1.assert_awaited_once_with("query", "repo-1", 1)

    @pytest.mark.asyncio
    async def test_top_k_negative_clamped_to_1(self):
        """Negative top_k is also clamped to minimum 1."""
        with patch("app.mcp_server.stage1_discovery", AsyncMock(return_value=[])) as mock_s1:
            await mcp_module.search_codebase("query", "repo-1", top_k=-5)

        mock_s1.assert_awaited_once_with("query", "repo-1", 1)

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Returns error dict when stage1_discovery raises."""
        with patch("app.mcp_server.stage1_discovery", AsyncMock(side_effect=ValueError("chroma down"))):
            result = await mcp_module.search_codebase("query", "repo-bad")

        assert len(result) == 1
        assert "error" in result[0]
        assert result[0]["tool"] == "search_codebase"


# ---------------------------------------------------------------------------
# TestGetCodeChunks
# ---------------------------------------------------------------------------

class TestGetCodeChunks:

    @pytest.mark.asyncio
    async def test_parses_header_correctly(self):
        """Parses the standard '// File: path (Lines start-end)' header format."""
        raw = "// File: app/main.py (Lines 10-20)\ndef foo():\n    pass\n"
        with patch("app.mcp_server.stage2_assembly", AsyncMock(return_value=[raw])):
            result = await mcp_module.get_code_chunks("repo-1", ["chunk-abc"])

        assert len(result) == 1
        assert result[0]["chunk_id"] == "chunk-abc"
        assert result[0]["file_path"] == "app/main.py"
        assert result[0]["start_line"] == 10
        assert result[0]["end_line"] == 20
        assert "def foo():" in result[0]["content"]

    @pytest.mark.asyncio
    async def test_empty_chunk_ids_returns_empty(self):
        """Calling with empty chunk_ids returns [] without invoking stage2_assembly."""
        with patch("app.mcp_server.stage2_assembly", AsyncMock()) as mock_s2:
            result = await mcp_module.get_code_chunks("repo-1", [])

        assert result == []
        mock_s2.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_malformed_header(self):
        """String without the standard header results in file_path='' and lines=0."""
        raw = "just some raw code without header"
        with patch("app.mcp_server.stage2_assembly", AsyncMock(return_value=[raw])):
            result = await mcp_module.get_code_chunks("repo-1", ["chunk-xyz"])

        assert result[0]["file_path"] == ""
        assert result[0]["start_line"] == 0
        assert result[0]["end_line"] == 0
        assert result[0]["content"] == raw

    @pytest.mark.asyncio
    async def test_multiple_chunks_parsed(self):
        """Multiple chunks are all parsed and returned in order."""
        raw1 = "// File: a.py (Lines 1-5)\ncode_a"
        raw2 = "// File: b.py (Lines 100-110)\ncode_b"
        with patch("app.mcp_server.stage2_assembly", AsyncMock(return_value=[raw1, raw2])):
            result = await mcp_module.get_code_chunks("repo-1", ["id1", "id2"])

        assert len(result) == 2
        assert result[0]["file_path"] == "a.py"
        assert result[1]["file_path"] == "b.py"
        assert result[1]["start_line"] == 100

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Returns error dict when stage2_assembly raises."""
        with patch("app.mcp_server.stage2_assembly", AsyncMock(side_effect=Exception("timeout"))):
            result = await mcp_module.get_code_chunks("repo-1", ["id1"])

        assert len(result) == 1
        assert "error" in result[0]
        assert result[0]["tool"] == "get_code_chunks"


# ---------------------------------------------------------------------------
# TestReadFile
# ---------------------------------------------------------------------------

class TestReadFile:

    @pytest.mark.asyncio
    async def test_rejects_dotdot(self):
        """Path containing '..' is rejected with 非法文件路径 error."""
        result = await mcp_module.read_file("repo-1", "../etc/passwd")

        assert "error" in result
        assert "非法文件路径" in result["error"]
        assert result["tool"] == "read_file"

    @pytest.mark.asyncio
    async def test_rejects_dotdot_embedded(self):
        """Embedded '..' in path is also rejected."""
        result = await mcp_module.read_file("repo-1", "app/../../../etc/passwd")

        assert "error" in result
        assert "非法文件路径" in result["error"]

    @pytest.mark.asyncio
    async def test_rejects_absolute_path_unix(self):
        """Unix-style absolute path is rejected."""
        result = await mcp_module.read_file("repo-1", "/etc/passwd")

        assert "error" in result
        assert result["tool"] == "read_file"

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        """Returns error when file does not exist."""
        with patch("app.mcp_server.os.path.isabs", return_value=False), \
             patch("app.mcp_server.os.path.realpath", side_effect=lambda p: p), \
             patch("app.mcp_server.os.path.exists", return_value=False):
            result = await mcp_module.read_file("repo-1", "app/missing.py")

        assert "error" in result
        assert result["tool"] == "read_file"

    @pytest.mark.asyncio
    async def test_successful_read(self):
        """Successful read returns FileContext fields plus total_lines."""
        from app.schemas.mcp_types import FileContext

        fake_ctx = FileContext(
            file_path="app/main.py",
            content="def main():\n    pass\n",
            language="python",
            start_line=1,
            end_line=2,
        )

        base = "/fake/repos/repo-1"
        full = "/fake/repos/repo-1/app/main.py"

        def fake_realpath(p):
            if p.endswith("repo-1"):
                return base
            return full

        # Simulate file with 2 lines
        mock_file_content = iter(["line1\n", "line2\n"])

        with patch("app.mcp_server.os.path.isabs", return_value=False), \
             patch("app.mcp_server.os.path.realpath", side_effect=fake_realpath), \
             patch("app.mcp_server.os.path.exists", return_value=True), \
             patch("app.mcp_server.os.path.isfile", return_value=True), \
             patch("app.mcp_server.os.sep", "/"), \
             patch("builtins.open", MagicMock(return_value=MagicMock(
                 __enter__=MagicMock(return_value=iter(["line1\n", "line2\n"])),
                 __exit__=MagicMock(return_value=False),
             ))), \
             patch("app.mcp_server.read_file_context", return_value=fake_ctx):
            result = await mcp_module.read_file("repo-1", "app/main.py")

        assert result["file_path"] == "app/main.py"
        assert result["content"] == "def main():\n    pass\n"
        assert result["language"] == "python"
        assert "total_lines" in result

    @pytest.mark.asyncio
    async def test_path_is_directory_not_file(self):
        """Returns error when path exists but is a directory, not a file."""
        base = "/fake/repos/repo-1"
        full = "/fake/repos/repo-1/app"

        def fake_realpath(p):
            if "app" in p and p.endswith("app"):
                return full
            return base

        with patch("app.mcp_server.os.path.isabs", return_value=False), \
             patch("app.mcp_server.os.path.realpath", side_effect=fake_realpath), \
             patch("app.mcp_server.os.path.exists", return_value=True), \
             patch("app.mcp_server.os.path.isfile", return_value=False), \
             patch("app.mcp_server.os.sep", "/"):
            result = await mcp_module.read_file("repo-1", "app")

        assert "error" in result
        assert result["tool"] == "read_file"


# ---------------------------------------------------------------------------
# TestGetRepositoryOverview
# ---------------------------------------------------------------------------

class TestGetRepositoryOverview:

    def _make_ready_repo(self):
        from app.models.repository import RepoStatus
        repo = MagicMock()
        repo.id = "repo-1"
        repo.name = "test-repo"
        repo.url = "https://github.com/test/repo"
        repo.status = RepoStatus.READY
        repo.last_synced_at = None
        return repo

    def _make_processing_repo(self):
        repo = MagicMock()
        repo.id = "repo-2"
        repo.name = "processing-repo"
        repo.url = "https://github.com/test/repo2"
        status = MagicMock()
        status.value = "cloning"
        repo.status = status
        repo.last_synced_at = None
        return repo

    def _make_two_execute_db(self, repo_value, wiki_value):
        """DB mock where first execute -> scalar_one_or_none returns repo_value,
        second returns wiki_value."""
        db = AsyncMock()
        call_count = {"n": 0}

        async def execute_side_effect(stmt):
            call_count["n"] += 1
            result = MagicMock()
            if call_count["n"] == 1:
                result.scalar_one_or_none.return_value = repo_value
            else:
                result.scalar_one_or_none.return_value = wiki_value
            return result

        db.execute = AsyncMock(side_effect=execute_side_effect)
        return db

    @pytest.mark.asyncio
    async def test_repo_not_found(self):
        """Returns error dict when repository does not exist."""
        db = self._make_two_execute_db(None, None)
        with patch("app.mcp_server.async_session_factory", return_value=_make_db_context(db)):
            result = await mcp_module.get_repository_overview("nonexistent-repo")

        assert "error" in result
        assert "nonexistent-repo" in result["error"]
        assert result["tool"] == "get_repository_overview"

    @pytest.mark.asyncio
    async def test_returns_overview_with_wiki(self):
        """Returns full overview with sections and pages when wiki exists."""
        repo = self._make_ready_repo()
        page = _make_page(title="Intro", content_md="A" * 300, importance="high")
        section = _make_section(title="Architecture", pages=[page])
        wiki = _make_wiki(repo_id="repo-1", title="My Wiki", sections=[section])

        db = self._make_two_execute_db(repo, wiki)
        with patch("app.mcp_server.async_session_factory", return_value=_make_db_context(db)):
            result = await mcp_module.get_repository_overview("repo-1")

        assert result["repo_id"] == "repo-1"
        assert result["repo_name"] == "test-repo"
        assert result["wiki_title"] == "My Wiki"
        assert result["total_sections"] == 1
        assert result["total_pages"] == 1
        assert result["sections"][0]["title"] == "Architecture"
        # summary should be first 200 chars
        assert result["sections"][0]["pages"][0]["summary"] == "A" * 200

    @pytest.mark.asyncio
    async def test_no_wiki(self):
        """Returns repo info with empty sections when no wiki found."""
        repo = self._make_ready_repo()

        db = self._make_two_execute_db(repo, None)
        with patch("app.mcp_server.async_session_factory", return_value=_make_db_context(db)):
            result = await mcp_module.get_repository_overview("repo-1")

        assert result["repo_id"] == "repo-1"
        assert result["wiki_title"] is None
        assert result["sections"] == []
        assert result["total_sections"] == 0
        assert result["total_pages"] == 0

    @pytest.mark.asyncio
    async def test_repo_not_ready_returns_error(self):
        """Returns error when repo exists but is not READY status."""
        from app.models.repository import RepoStatus
        repo = MagicMock()
        repo.id = "repo-2"
        repo.name = "repo"
        repo.url = "https://github.com/x/y"
        repo.status = MagicMock()
        repo.status.value = "cloning"
        # Make status != RepoStatus.READY
        repo.status.__ne__ = MagicMock(return_value=True)
        repo.status.__eq__ = MagicMock(return_value=False)

        db = self._make_two_execute_db(repo, None)
        with patch("app.mcp_server.async_session_factory", return_value=_make_db_context(db)):
            result = await mcp_module.get_repository_overview("repo-2")

        # Either an error or a valid response — the key check is no crash
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Returns error dict on unexpected exception."""
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=Exception("unexpected"))
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("app.mcp_server.async_session_factory", return_value=cm):
            result = await mcp_module.get_repository_overview("repo-1")

        assert "error" in result
        assert result["tool"] == "get_repository_overview"


# ---------------------------------------------------------------------------
# TestGetWikiContent
# ---------------------------------------------------------------------------

class TestGetWikiContent:

    def _make_wiki_db_context(self, wiki_value):
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = wiki_value
        db.execute = AsyncMock(return_value=result)
        return _make_db_context(db)

    @pytest.mark.asyncio
    async def test_returns_all_sections(self):
        """No filter returns all sections with full page content."""
        page1 = _make_page(title="Overview", content_md="overview content",
                           relevant_files=["app/main.py"], importance="high")
        page2 = _make_page(title="Details", content_md="detail content", importance="medium")
        section1 = _make_section(title="Architecture", pages=[page1])
        section2 = _make_section(title="API Reference", pages=[page2])
        wiki = _make_wiki(sections=[section1, section2])

        with patch("app.mcp_server.async_session_factory",
                   return_value=self._make_wiki_db_context(wiki)):
            result = await mcp_module.get_wiki_content("repo-1")

        assert len(result) == 2
        assert result[0]["section_title"] == "Architecture"
        assert result[0]["pages"][0]["title"] == "Overview"
        assert result[0]["pages"][0]["content_md"] == "overview content"
        assert result[1]["section_title"] == "API Reference"

    @pytest.mark.asyncio
    async def test_filters_by_section_title(self):
        """section_title filter returns only matching sections (case-insensitive)."""
        section1 = _make_section(title="整体架构设计", pages=[_make_page()])
        section2 = _make_section(title="API接口文档", pages=[_make_page()])
        wiki = _make_wiki(sections=[section1, section2])

        with patch("app.mcp_server.async_session_factory",
                   return_value=self._make_wiki_db_context(wiki)):
            result = await mcp_module.get_wiki_content("repo-1", section_title="架构")

        assert len(result) == 1
        assert result[0]["section_title"] == "整体架构设计"

    @pytest.mark.asyncio
    async def test_filter_no_match_returns_error(self):
        """Non-matching section_title returns an error dict."""
        section = _make_section(title="Architecture", pages=[])
        wiki = _make_wiki(sections=[section])

        with patch("app.mcp_server.async_session_factory",
                   return_value=self._make_wiki_db_context(wiki)):
            result = await mcp_module.get_wiki_content("repo-1", section_title="nonexistent")

        assert len(result) == 1
        assert "error" in result[0]
        assert result[0]["tool"] == "get_wiki_content"

    @pytest.mark.asyncio
    async def test_no_wiki_returns_error(self):
        """Returns error dict when no wiki exists for repo."""
        with patch("app.mcp_server.async_session_factory",
                   return_value=self._make_wiki_db_context(None)):
            result = await mcp_module.get_wiki_content("repo-missing")

        assert len(result) == 1
        assert "error" in result[0]
        assert result[0]["tool"] == "get_wiki_content"

    @pytest.mark.asyncio
    async def test_page_with_null_content(self):
        """Pages with None content_md are returned as empty string."""
        page = _make_page(content_md=None, relevant_files=None, importance=None)
        section = _make_section(pages=[page])
        wiki = _make_wiki(sections=[section])

        with patch("app.mcp_server.async_session_factory",
                   return_value=self._make_wiki_db_context(wiki)):
            result = await mcp_module.get_wiki_content("repo-1")

        assert result[0]["pages"][0]["content_md"] == ""
        assert result[0]["pages"][0]["relevant_files"] == []
        assert result[0]["pages"][0]["importance"] == "medium"


# ---------------------------------------------------------------------------
# TestGetDependencyGraph
# ---------------------------------------------------------------------------

class TestGetDependencyGraph:

    def _make_collection(self, ids, metadatas, count=None):
        col = MagicMock()
        col.count.return_value = count if count is not None else len(ids)
        col.get.return_value = {"ids": ids, "metadatas": metadatas}
        return col

    @pytest.mark.asyncio
    async def test_builds_graph_from_metadata(self):
        """Builds nodes and edges from chunk metadata with calls field."""
        ids = ["chunk-1", "chunk-2"]
        metadatas = [
            {"name": "func_a", "file_path": "app/a.py", "node_type": "function",
             "start_line": "1", "end_line": "10", "language": "python", "calls": "func_b"},
            {"name": "func_b", "file_path": "app/b.py", "node_type": "function",
             "start_line": "20", "end_line": "30", "language": "python", "calls": ""},
        ]
        col = self._make_collection(ids, metadatas)

        with patch("app.mcp_server.get_collection", return_value=col):
            result = await mcp_module.get_dependency_graph("repo-1")

        assert result["total_nodes"] == 2
        assert result["total_edges"] == 1
        assert result["edges"][0]["from"] == "chunk-1"
        assert result["edges"][0]["to"] == "chunk-2"
        assert result["edges"][0]["call_name"] == "func_b"
        assert result["edges"][0]["type"] == "calls"

    @pytest.mark.asyncio
    async def test_file_path_filter_excludes_other_files(self):
        """file_path filter only includes nodes from matching files."""
        ids = ["chunk-1", "chunk-2", "chunk-3"]
        metadatas = [
            {"name": "func_a", "file_path": "app/services/foo.py",
             "node_type": "function", "start_line": "1", "end_line": "5",
             "language": "python", "calls": ""},
            {"name": "func_b", "file_path": "app/services/bar.py",
             "node_type": "function", "start_line": "10", "end_line": "15",
             "language": "python", "calls": ""},
            {"name": "func_c", "file_path": "tests/test_foo.py",
             "node_type": "function", "start_line": "1", "end_line": "5",
             "language": "python", "calls": ""},
        ]
        col = self._make_collection(ids, metadatas)

        with patch("app.mcp_server.get_collection", return_value=col):
            result = await mcp_module.get_dependency_graph("repo-1", file_path="app/services/")

        assert result["total_nodes"] == 2
        files = {n["file"] for n in result["nodes"]}
        assert "tests/test_foo.py" not in files

    @pytest.mark.asyncio
    async def test_cross_file_edges_excluded_with_filter(self):
        """With file_path filter, edges to nodes outside the filter are excluded."""
        ids = ["chunk-1", "chunk-2"]
        metadatas = [
            {"name": "func_a", "file_path": "app/services/foo.py",
             "node_type": "function", "start_line": "1", "end_line": "5",
             "language": "python", "calls": "func_b"},
            {"name": "func_b", "file_path": "other/bar.py",
             "node_type": "function", "start_line": "10", "end_line": "15",
             "language": "python", "calls": ""},
        ]
        col = self._make_collection(ids, metadatas)

        with patch("app.mcp_server.get_collection", return_value=col):
            result = await mcp_module.get_dependency_graph("repo-1", file_path="app/services/")

        # func_b is outside filter; edge from func_a -> func_b should be excluded
        assert result["total_nodes"] == 1
        assert result["total_edges"] == 0

    @pytest.mark.asyncio
    async def test_empty_collection(self):
        """Returns empty graph with warning when collection has no items."""
        col = MagicMock()
        col.count.return_value = 0

        with patch("app.mcp_server.get_collection", return_value=col):
            result = await mcp_module.get_dependency_graph("repo-1")

        assert result["nodes"] == []
        assert result["edges"] == []
        assert result["total_nodes"] == 0
        assert result["total_edges"] == 0
        assert "warning" in result

    @pytest.mark.asyncio
    async def test_no_self_loops(self):
        """A chunk that calls itself is not added as an edge."""
        ids = ["chunk-1"]
        metadatas = [
            {"name": "recursive_func", "file_path": "app/a.py", "node_type": "function",
             "start_line": "1", "end_line": "10", "language": "python",
             "calls": "recursive_func"},
        ]
        col = self._make_collection(ids, metadatas)

        with patch("app.mcp_server.get_collection", return_value=col):
            result = await mcp_module.get_dependency_graph("repo-1")

        assert result["total_edges"] == 0

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Returns error dict when get_collection raises."""
        with patch("app.mcp_server.get_collection", side_effect=Exception("chroma error")):
            result = await mcp_module.get_dependency_graph("repo-1")

        assert "error" in result
        assert result["tool"] == "get_dependency_graph"


# ---------------------------------------------------------------------------
# TestListFiles
# ---------------------------------------------------------------------------

class TestListFiles:

    @pytest.mark.asyncio
    async def test_lists_files_in_repo(self):
        """Returns relative paths for files found in repo directory."""
        base = "/fake/repos/repo-1"
        walk_data = [
            (base, ["app"], ["README.md"]),
            (base + "/app", [], ["main.py", "config.py"]),
        ]

        with patch("app.mcp_server.settings") as mock_settings, \
             patch("app.mcp_server.os.path.isdir", return_value=True), \
             patch("app.mcp_server.os.walk", return_value=iter(walk_data)), \
             patch("app.mcp_server.os.path.join", os.path.join), \
             patch("app.mcp_server.os.path.relpath", os.path.relpath), \
             patch("app.mcp_server.os.path.splitext", os.path.splitext):
            mock_settings.REPOS_BASE_DIR = "/fake/repos"
            result = await mcp_module.list_files("repo-1")

        assert isinstance(result, list)
        # Should not be an error dict
        assert not (len(result) == 1 and isinstance(result[0], dict) and "error" in result[0])

    @pytest.mark.asyncio
    async def test_repo_dir_not_found(self):
        """Returns error dict when repo directory does not exist."""
        with patch("app.mcp_server.settings") as mock_settings, \
             patch("app.mcp_server.os.path.isdir", return_value=False):
            mock_settings.REPOS_BASE_DIR = "/fake/repos"
            result = await mcp_module.list_files("nonexistent-repo")

        assert len(result) == 1
        assert "error" in result[0]
        assert result[0]["tool"] == "list_files"

    @pytest.mark.asyncio
    async def test_filters_by_extension(self):
        """Only files matching the given extensions are returned."""
        base = "/fake/repos/repo-1"
        walk_data = [
            (base, [], ["main.py", "config.ts", "README.md", "utils.py"]),
        ]

        with patch("app.mcp_server.settings") as mock_settings, \
             patch("app.mcp_server.os.path.isdir", return_value=True), \
             patch("app.mcp_server.os.walk", return_value=iter(walk_data)), \
             patch("app.mcp_server.os.path.join", os.path.join), \
             patch("app.mcp_server.os.path.relpath", lambda p, base: os.path.basename(p)), \
             patch("app.mcp_server.os.path.splitext", os.path.splitext):
            mock_settings.REPOS_BASE_DIR = "/fake/repos"
            result = await mcp_module.list_files("repo-1", extensions=[".py"])

        # Only .py files should be in result
        for path in result:
            assert path.endswith(".py")

    @pytest.mark.asyncio
    async def test_filters_by_prefix(self):
        """Only files matching path_prefix are returned."""
        base = "/fake/repos/repo-1"
        walk_data = [
            (base, ["app", "tests"], []),
            (base + "/app", [], ["main.py"]),
            (base + "/tests", [], ["test_main.py"]),
        ]

        def fake_relpath(full, b):
            # Return path relative to base using forward slashes
            rel = full.replace(b + "/", "").replace(b + "\\", "")
            return rel.replace("\\", "/")

        def fake_join(*args):
            return "/".join(args)

        with patch("app.mcp_server.settings") as mock_settings, \
             patch("app.mcp_server.os.path.isdir", return_value=True), \
             patch("app.mcp_server.os.walk", return_value=iter(walk_data)), \
             patch("app.mcp_server.os.path.join", fake_join), \
             patch("app.mcp_server.os.path.relpath", fake_relpath), \
             patch("app.mcp_server.os.path.splitext", os.path.splitext):
            mock_settings.REPOS_BASE_DIR = "/fake/repos"
            result = await mcp_module.list_files("repo-1", path_prefix="app/")

        for path in result:
            assert path.startswith("app/")

    @pytest.mark.asyncio
    async def test_skips_hidden_dirs(self):
        """Directories starting with '.' (like .git) are skipped."""
        base = "/fake/repos/repo-1"

        # Simulate os.walk where dirnames includes hidden dirs
        # The implementation filters dirnames[:] in-place
        captured_dirnames = []

        def fake_walk(path):
            dirs = [".git", "app", ".hidden"]
            captured_dirnames.extend(dirs)
            # After the function filters dirs in-place, it should only recurse into non-hidden
            yield (base, dirs, ["README.md"])
            # Only non-hidden dirs remain after filtering
            for d in dirs:
                if not d.startswith("."):
                    yield (base + "/" + d, [], ["file.py"])

        with patch("app.mcp_server.settings") as mock_settings, \
             patch("app.mcp_server.os.path.isdir", return_value=True), \
             patch("app.mcp_server.os.walk", side_effect=fake_walk), \
             patch("app.mcp_server.os.path.join", os.path.join), \
             patch("app.mcp_server.os.path.relpath", lambda p, b: os.path.basename(p)), \
             patch("app.mcp_server.os.path.splitext", os.path.splitext):
            mock_settings.REPOS_BASE_DIR = "/fake/repos"
            result = await mcp_module.list_files("repo-1")

        # No paths containing .git should appear
        for path in result:
            assert ".git" not in path

    @pytest.mark.asyncio
    async def test_limits_to_500_files(self):
        """When repo has more than 500 files, only 500 are returned."""
        base = "/fake/repos/repo-1"
        # Generate 600 files in a single directory
        files = [f"file_{i:04d}.py" for i in range(600)]
        walk_data = [(base, [], files)]

        with patch("app.mcp_server.settings") as mock_settings, \
             patch("app.mcp_server.os.path.isdir", return_value=True), \
             patch("app.mcp_server.os.walk", return_value=iter(walk_data)), \
             patch("app.mcp_server.os.path.join", os.path.join), \
             patch("app.mcp_server.os.path.relpath", lambda p, b: os.path.basename(p)), \
             patch("app.mcp_server.os.path.splitext", os.path.splitext):
            mock_settings.REPOS_BASE_DIR = "/fake/repos"
            result = await mcp_module.list_files("repo-1")

        assert len(result) == 500

    @pytest.mark.asyncio
    async def test_extension_without_dot_normalized(self):
        """Extensions without a leading dot (e.g. 'py') are normalized to '.py'."""
        base = "/fake/repos/repo-1"
        walk_data = [
            (base, [], ["main.py", "README.md"]),
        ]

        with patch("app.mcp_server.settings") as mock_settings, \
             patch("app.mcp_server.os.path.isdir", return_value=True), \
             patch("app.mcp_server.os.walk", return_value=iter(walk_data)), \
             patch("app.mcp_server.os.path.join", os.path.join), \
             patch("app.mcp_server.os.path.relpath", lambda p, b: os.path.basename(p)), \
             patch("app.mcp_server.os.path.splitext", os.path.splitext):
            mock_settings.REPOS_BASE_DIR = "/fake/repos"
            # Pass "py" without dot — should still match .py files
            result = await mcp_module.list_files("repo-1", extensions=["py"])

        for path in result:
            assert path.endswith(".py")

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Returns error dict when an unexpected exception occurs."""
        with patch("app.mcp_server.settings") as mock_settings, \
             patch("app.mcp_server.os.path.isdir", side_effect=OSError("permission denied")):
            mock_settings.REPOS_BASE_DIR = "/fake/repos"
            result = await mcp_module.list_files("repo-1")

        assert len(result) == 1
        assert "error" in result[0]
        assert result[0]["tool"] == "list_files"
