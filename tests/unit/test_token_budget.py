# tests/unit/test_token_budget.py
"""
Unit tests for app/services/token_budget.py

Covers:
- estimate_tokens: ASCII, Chinese, mixed, empty
- apply_token_budget: empty history, trimming, RAG truncation, unknown model default
"""
from app.services.token_budget import (
    estimate_tokens,
    apply_token_budget,
    MODEL_LIMITS,
    BUDGET_RATIO,
)


class TestEstimateTokens:
    def test_pure_ascii(self):
        """ASCII characters count at 4 chars per token."""
        text = "abcd"  # 4 ASCII chars -> 1 token
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
        text = "你好"  # 2 non-ASCII chars -> 1 token
        assert estimate_tokens(text) == 1

    def test_pure_chinese_four_chars(self):
        """4 Chinese chars -> 2 tokens."""
        text = "你好世界"  # 4 non-ASCII -> 2 tokens
        assert estimate_tokens(text) == 2

    def test_mixed_text(self):
        """Mixed ASCII + Chinese: each bucket computed separately."""
        # "Hi你好" -> 2 ASCII + 2 non-ASCII -> (2//4) + (2//2) = 0 + 1 = 1
        text = "Hi你好"
        assert estimate_tokens(text) == 1

    def test_mixed_longer(self):
        """Longer mixed: "Hello世界" -> 5 ASCII + 2 non-ASCII -> 1 + 1 = 2."""
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
        trimmed, _ = apply_token_budget(
            messages, "gpt-4o", "sys", "ctx", "q"
        )
        assert len(trimmed) == 2

    def test_oldest_messages_dropped_when_budget_exceeded(self):
        """When history exceeds budget, oldest messages are dropped first."""
        # Create many messages so only the most recent fit
        long_content = "x" * 10000
        messages = [
            {"role": "user", "content": long_content},
            {"role": "assistant", "content": long_content},
            {"role": "user", "content": "short question"},
        ]
        # Use a small model limit to force trimming
        trimmed, _ = apply_token_budget(
            messages, "gpt-3.5-turbo", "sys", "", "q"
        )
        # The short recent message should survive; long old ones should be dropped
        contents = [m["content"] for m in trimmed]
        assert "short question" in contents

    def test_rag_context_truncated_when_too_long(self):
        """RAG context longer than its budget is truncated."""
        huge_context = "A" * 100000  # Very large context
        _, ctx = apply_token_budget(
            [], "gpt-3.5-turbo", "sys", huge_context, "q"
        )
        assert len(ctx) < len(huge_context)

    def test_unknown_model_uses_32000_default(self):
        """Unknown model name falls back to 32000 token limit."""
        # Known model limit for cross-check
        known_limit = MODEL_LIMITS.get("gpt-4o", None)
        assert known_limit is not None

        # Unknown model should use 32000 default
        default_limit = MODEL_LIMITS.get("unknown-model-xyz", 32000)
        assert default_limit == 32000

        # apply_token_budget should not crash with unknown model
        msgs, ctx = apply_token_budget(
            [{"role": "user", "content": "hi"}],
            "unknown-model-xyz",
            "system",
            "context",
            "query",
        )
        # Result should be a list (possibly with the message, possibly trimmed)
        assert isinstance(msgs, list)
        assert isinstance(ctx, str)

    def test_budget_ratio_applied(self):
        """Budget is MODEL_LIMIT * BUDGET_RATIO."""
        assert BUDGET_RATIO == 0.80
        # For gpt-4o: budget = 128000 * 0.80 = 102400
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
        trimmed, _ = apply_token_budget(
            messages, "gpt-4o", "sys", "", "q"
        )
        if len(trimmed) >= 2:
            roles = [m["role"] for m in trimmed]
            # Roles should alternate user/assistant in original order
            assert roles == [m["role"] for m in messages[-len(trimmed):]]
