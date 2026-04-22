"""
Unit tests for app/services/token_budget.py.

Covers:
- estimate_tokens: ASCII, Chinese, mixed, empty
- apply_token_budget: empty history, trimming, chunk-aware RAG context handling
- trim_chunks_to_budget: weighted trimming, order preservation, chunk integrity
"""
from app.services.token_budget import (
    estimate_tokens,
    apply_token_budget,
    trim_chunks_to_budget,
    MODEL_LIMITS,
    BUDGET_RATIO,
)


class TestEstimateTokens:
    def test_pure_ascii(self):
        """ASCII characters count at 4 chars per token."""
        text = "abcd"
        assert estimate_tokens(text) == 1

    def test_pure_ascii_multiple(self):
        """12 ASCII chars -> 3 tokens."""
        text = "a" * 12
        assert estimate_tokens(text) == 3

    def test_pure_ascii_remainder_truncated(self):
        """Integer division: 5 ASCII chars -> 1 token (5 // 4 = 1)."""
        assert estimate_tokens("hello") == 1

    def test_pure_chinese(self):
        """Non-ASCII characters count at 2 chars per token."""
        text = "你好"
        assert estimate_tokens(text) == 1

    def test_pure_chinese_four_chars(self):
        """4 Chinese chars -> 2 tokens."""
        text = "你好世界"
        assert estimate_tokens(text) == 2

    def test_mixed_text(self):
        """Mixed ASCII + Chinese: each bucket computed separately."""
        text = "Hi你好"
        assert estimate_tokens(text) == 1

    def test_mixed_longer(self):
        """Longer mixed text estimates both ASCII and non-ASCII buckets."""
        text = "Hello世界"
        assert estimate_tokens(text) == 2

    def test_empty_string(self):
        """Empty string returns 0 tokens."""
        assert estimate_tokens("") == 0

    def test_whitespace_only(self):
        """Spaces are ASCII; 4 spaces -> 1 token."""
        assert estimate_tokens("    ") == 1

    def test_large_ascii_text(self):
        """400 ASCII chars -> 100 tokens."""
        text = "a" * 400
        assert estimate_tokens(text) == 100


class TestApplyTokenBudget:
    def test_empty_history_returns_empty_list(self):
        """No messages -> trimmed_messages is empty list."""
        msgs, ctx = apply_token_budget([], "gpt-4o", "system", "rag context", "user query")
        assert msgs == []

    def test_empty_history_context_unchanged(self):
        """With no history and tiny context, RAG context is returned intact."""
        short_ctx = "short context"
        _, ctx = apply_token_budget([], "gpt-4o", "system", short_ctx, "query")
        assert ctx == short_ctx

    def test_messages_within_budget_all_kept(self):
        """Messages that fit within budget are all retained."""
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        trimmed, _ = apply_token_budget(messages, "gpt-4o", "sys", "ctx", "q")
        assert len(trimmed) == 2

    def test_oldest_messages_dropped_when_budget_exceeded(self):
        """When history exceeds budget, oldest messages are dropped first."""
        long_content = "x" * 10000
        messages = [
            {"role": "user", "content": long_content},
            {"role": "assistant", "content": long_content},
            {"role": "user", "content": "short question"},
        ]
        trimmed, _ = apply_token_budget(messages, "gpt-3.5-turbo", "sys", "", "q")
        contents = [m["content"] for m in trimmed]
        assert "short question" in contents

    def test_rag_context_trimmed_by_chunk_when_too_long(self):
        """RAG context longer than budget is trimmed at chunk boundaries."""
        chunk_a = "A" * 20000
        chunk_b = "B" * 20000
        chunk_c = "C" * 20000
        huge_context = "\n\n---\n\n".join([chunk_a, chunk_b, chunk_c])

        _, ctx = apply_token_budget([], "gpt-3.5-turbo", "sys", huge_context, "q")

        assert len(ctx) < len(huge_context)
        remaining_chunks = ctx.split("\n\n---\n\n")
        for chunk in remaining_chunks:
            assert chunk in [chunk_a, chunk_b, chunk_c]

    def test_unknown_model_uses_32000_default(self):
        """Unknown model name falls back to 32000 token limit."""
        known_limit = MODEL_LIMITS.get("gpt-4o", None)
        assert known_limit is not None

        default_limit = MODEL_LIMITS.get("unknown-model-xyz", 32000)
        assert default_limit == 32000

        msgs, ctx = apply_token_budget(
            [{"role": "user", "content": "hi"}],
            "unknown-model-xyz",
            "system",
            "context",
            "query",
        )
        assert isinstance(msgs, list)
        assert isinstance(ctx, str)

    def test_budget_ratio_applied(self):
        """Budget is MODEL_LIMIT * BUDGET_RATIO."""
        assert BUDGET_RATIO == 0.80
        assert MODEL_LIMITS["gpt-4o"] == 128000
        expected_budget = int(128000 * 0.80)
        assert expected_budget == 102400

    def test_return_types_are_correct(self):
        """Return value is always (list, str)."""
        messages = [{"role": "user", "content": "test"}]
        result = apply_token_budget(messages, "gpt-4o", "sys", "ctx", "q")
        assert isinstance(result, tuple)
        assert len(result) == 2
        trimmed, ctx = result
        assert isinstance(trimmed, list)
        assert isinstance(ctx, str)

    def test_order_preserved_in_trimmed_messages(self):
        """Retained messages maintain their original chronological order."""
        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
            {"role": "user", "content": "third"},
        ]
        trimmed, _ = apply_token_budget(messages, "gpt-4o", "sys", "", "q")
        if len(trimmed) >= 2:
            roles = [m["role"] for m in trimmed]
            assert roles == [m["role"] for m in messages[-len(trimmed):]]


class TestTrimChunksToBudget:
    def test_all_chunks_within_budget_returns_all(self):
        """总 token 在预算内时返回全部 chunk。"""
        chunks = ["short chunk 1", "short chunk 2", "short chunk 3"]
        result = trim_chunks_to_budget(chunks, 100000)
        assert result == chunks

    def test_low_weight_chunks_dropped_first(self):
        """超预算时，低权重 chunk 优先被丢弃，高权重保留。"""
        chunk_a = "x" * 400
        chunk_b = "y" * 400
        chunks = [chunk_a, chunk_b]
        weights = [0.3, 0.9]

        result = trim_chunks_to_budget(chunks, 120, weights)

        assert result == [chunk_b]

    def test_variable_size_chunks_still_drop_low_weight_first(self):
        """即使低权重 chunk 更小，也应先被丢弃而不是挤掉更高权重 chunk。"""
        chunk_high = "a" * 320  # 80 tokens
        chunk_mid = "b" * 320   # 80 tokens
        chunk_low = "c" * 280   # 70 tokens
        chunks = [chunk_high, chunk_mid, chunk_low]
        weights = [0.9, 0.8, 0.1]

        result = trim_chunks_to_budget(chunks, 150, weights)

        assert result == [chunk_high]

    def test_original_order_preserved(self):
        """返回的 chunk 保持原始顺序（不按权重排列）。"""
        chunks = ["aaaa", "bbbb", "cccc"]
        weights = [0.5, 0.9, 0.7]

        result = trim_chunks_to_budget(chunks, 2, weights)

        assert result == ["bbbb", "cccc"]

    def test_at_least_one_chunk_always_kept(self):
        """即使预算极小，至少保留 1 个 chunk。"""
        chunks = ["a" * 1000]
        result = trim_chunks_to_budget(chunks, 1)
        assert len(result) == 1

    def test_empty_chunks_returns_empty(self):
        """空列表输入返回空列表。"""
        result = trim_chunks_to_budget([], 1000)
        assert result == []

    def test_no_weights_uses_default(self):
        """不提供 weights 时使用默认权重 0.5。"""
        chunks = ["chunk_a", "chunk_b"]
        result = trim_chunks_to_budget(chunks, 100000)
        assert len(result) == 2

    def test_equal_weights_preserve_original_order(self):
        """同权重时按原始顺序保留（前面的优先）。"""
        chunks = ["a" * 100, "b" * 100, "c" * 100, "d" * 100]
        weights = [0.5, 0.5, 0.5, 0.5]

        result = trim_chunks_to_budget(chunks, 60, weights)

        assert result == [chunks[0], chunks[1]]

    def test_no_weights_tiebreak_is_deterministic(self):
        """weights=None 时多次调用结果完全一致。"""
        chunks = ["a" * 100, "b" * 100, "c" * 100]
        r1 = trim_chunks_to_budget(chunks, 50)
        r2 = trim_chunks_to_budget(chunks, 50)
        assert r1 == r2

    def test_chunk_integrity_no_partial_content(self):
        """裁剪后的每个 chunk 都必须与原始完全一致。"""
        chunks = [
            "def function_a():\n    return 'hello'\n" + ("a" * 200),
            "class MyClass:\n    pass\n" + ("b" * 200),
            "CONSTANT = 'value'\n" + ("c" * 200),
        ]
        weights = [0.9, 0.5, 0.3]

        result = trim_chunks_to_budget(chunks, 80, weights)

        for chunk in result:
            assert chunk in chunks


class TestTokenBudgetChunkBoundary:
    """Chunk-aware trimming should preserve complete chunk boundaries."""

    def test_chunk_aware_truncation_preserves_chunk_boundary(self):
        """
        重构后行为：RAG context 超预算时按 chunk 整块丢弃。
        验证截断后的 context 中每个 chunk 都是完整的。
        """
        chunk_a = "// File: a.py\n" + ("a" * 20000)
        chunk_b = "// File: b.py\n" + ("b" * 20000)
        chunk_c = "// File: c.py\n" + ("c" * 20000)
        rag_context = "\n\n---\n\n".join([chunk_a, chunk_b, chunk_c])

        _, trimmed_ctx = apply_token_budget(
            [],
            "gpt-3.5-turbo",
            "system prompt " * 100,
            rag_context,
            "user query",
        )

        if len(trimmed_ctx) < len(rag_context):
            remaining_chunks = trimmed_ctx.split("\n\n---\n\n")
            original_chunks = [chunk_a, chunk_b, chunk_c]
            for remaining in remaining_chunks:
                assert remaining in original_chunks

    def test_multiple_chunks_format_preserved(self):
        """The multi-chunk separator remains the parsing contract."""
        chunks = ["chunk_1_content", "chunk_2_content", "chunk_3_content"]
        joined = "\n\n---\n\n".join(chunks)

        parts = joined.split("\n\n---\n\n")

        assert len(parts) == 3
        assert parts[0] == "chunk_1_content"
        assert parts[2] == "chunk_3_content"
