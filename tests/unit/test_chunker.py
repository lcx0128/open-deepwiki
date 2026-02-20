"""
模块二单元测试：滑动窗口切块器
"""
import pytest
from app.services.chunker import split_large_chunk, MAX_CHUNK_TOKENS
from app.schemas.chunk_node import ChunkNode


class TestSlidingWindowChunker:

    def test_small_chunk_unchanged(self):
        """小 chunk 不应被切分"""
        chunk = ChunkNode(
            content="def foo():\n    return 42\n",
            name="foo",
            file_path="test.py",
            node_type="function_definition",
            language="python",
            start_line=1,
            end_line=2,
        )
        result = split_large_chunk(chunk)
        assert len(result) == 1
        assert result[0].id == chunk.id

    def test_large_chunk_split(self):
        """大 chunk 应被切分为多个子 chunk"""
        # 生成一个很大的 chunk（约 50000 字符，远超 MAX_CHUNK_TOKENS*4）
        lines = [f"    line_{i} = {i} * 2  # computation" for i in range(1000)]
        content = "def huge_function():\n" + "\n".join(lines)
        chunk = ChunkNode(
            content=content,
            name="huge_function",
            file_path="big.py",
            node_type="function_definition",
            language="python",
            start_line=1,
            end_line=1001,
        )
        result = split_large_chunk(chunk)
        assert len(result) > 1
        # 验证子 chunk 的 parent 指向原 chunk
        for sub in result:
            assert sub.parent_id == chunk.id
            assert sub.parent_name == chunk.name
        # 验证命名
        assert result[0].name == "huge_function__part_0"
        assert result[1].name == "huge_function__part_1"

    def test_sub_chunk_node_type_has_part_suffix(self):
        """切分后子 chunk 的 node_type 应有 _part 后缀"""
        lines = ["    x = i" for i in range(2000)]
        content = "def big():\n" + "\n".join(lines)
        chunk = ChunkNode(
            content=content,
            name="big",
            file_path="test.py",
            node_type="function_definition",
            language="python",
            start_line=1,
            end_line=2001,
        )
        result = split_large_chunk(chunk)
        if len(result) > 1:
            assert result[0].node_type == "function_definition_part"

    def test_first_sub_chunk_has_calls(self):
        """第一个子 chunk 应继承原 chunk 的 calls"""
        lines = ["    x = i" for i in range(2000)]
        content = "def big():\n" + "\n".join(lines)
        chunk = ChunkNode(
            content=content,
            name="big",
            file_path="test.py",
            node_type="function_definition",
            language="python",
            calls=["helper", "util"],
            start_line=1,
            end_line=2001,
        )
        result = split_large_chunk(chunk)
        if len(result) > 1:
            assert result[0].calls == ["helper", "util"]
            # 其他子 chunk 不应有 calls
            assert result[1].calls == []

    def test_first_sub_chunk_has_docstring(self):
        """第一个子 chunk 应继承原 chunk 的 docstring"""
        lines = ["    x = i" for i in range(2000)]
        content = "def big():\n" + "\n".join(lines)
        chunk = ChunkNode(
            content=content,
            name="big",
            file_path="test.py",
            node_type="function_definition",
            language="python",
            docstring="This is a big function.",
            start_line=1,
            end_line=2001,
        )
        result = split_large_chunk(chunk)
        if len(result) > 1:
            assert result[0].docstring == "This is a big function."
            assert result[1].docstring is None

    def test_sub_chunk_language_preserved(self):
        """子 chunk 应保持原有语言"""
        lines = ["    x = i" for i in range(2000)]
        content = "def big():\n" + "\n".join(lines)
        chunk = ChunkNode(
            content=content,
            name="big",
            file_path="test.py",
            node_type="function_definition",
            language="python",
            start_line=1,
            end_line=2001,
        )
        result = split_large_chunk(chunk)
        for sub in result:
            assert sub.language == "python"

    def test_threshold_boundary(self):
        """刚好在阈值边界的 chunk 不应被切分"""
        # MAX_CHUNK_TOKENS * 4 个字符，即刚好在边界
        content = "x" * (MAX_CHUNK_TOKENS * 4)
        chunk = ChunkNode(
            content=content,
            name="boundary",
            file_path="test.py",
            node_type="function_definition",
            language="python",
            start_line=1,
            end_line=100,
        )
        result = split_large_chunk(chunk)
        assert len(result) == 1
        assert result[0].id == chunk.id
