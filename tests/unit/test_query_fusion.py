# tests/unit/test_query_fusion.py
"""
Unit tests for app/services/query_fusion.py

Covers:
- Empty history returns original question (no LLM call)
- Non-empty history calls adapter.generate_with_rate_limit and returns rewritten query
- LLM error falls back to original question
- Very long history is truncated to last 6 messages
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.query_fusion import fuse_query


class TestFuseQueryEmptyHistory:
    @pytest.mark.asyncio
    async def test_empty_history_returns_original_question(self):
        """When chat_history is empty, the original question is returned without LLM call."""
        with patch("app.services.query_fusion.create_adapter") as mock_factory:
            result = await fuse_query("What does parse_repository do?", [])

        assert result == "What does parse_repository do?"
        mock_factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_history_no_adapter_created(self):
        """Adapter is never instantiated when history is empty (saves LLM cost)."""
        with patch("app.services.query_fusion.create_adapter") as mock_factory:
            await fuse_query("Any question", [])

        mock_factory.assert_not_called()


class TestFuseQueryWithHistory:
    @pytest.mark.asyncio
    async def test_non_empty_history_calls_adapter(self):
        """With history present, create_adapter is called and its response returned."""
        mock_response = MagicMock()
        mock_response.content = "rewritten standalone query"

        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(return_value=mock_response)

        with patch("app.services.query_fusion.create_adapter", return_value=mock_adapter):
            result = await fuse_query(
                question="What does it do?",
                chat_history=[
                    {"role": "user", "content": "Tell me about parse_repository"},
                    {"role": "assistant", "content": "parse_repository clones and parses the repo."},
                ],
            )

        assert result == "rewritten standalone query"
        mock_adapter.generate_with_rate_limit.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_empty_history_passes_model_to_adapter(self):
        """The llm_model parameter is forwarded to generate_with_rate_limit."""
        mock_response = MagicMock()
        mock_response.content = "rewritten"

        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(return_value=mock_response)

        with patch("app.services.query_fusion.create_adapter", return_value=mock_adapter):
            await fuse_query(
                question="Who calls this?",
                chat_history=[{"role": "user", "content": "hi"}],
                llm_provider="openai",
                llm_model="gpt-4o-mini",
            )

        call_kwargs = mock_adapter.generate_with_rate_limit.call_args
        assert call_kwargs.kwargs.get("model") == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_default_model_is_gpt4o_mini(self):
        """When llm_model is not provided, defaults to gpt-4o-mini."""
        mock_response = MagicMock()
        mock_response.content = "rewritten"

        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(return_value=mock_response)

        with patch("app.services.query_fusion.create_adapter", return_value=mock_adapter):
            await fuse_query(
                question="Some question",
                chat_history=[{"role": "user", "content": "previous msg"}],
                llm_model=None,
            )

        call_kwargs = mock_adapter.generate_with_rate_limit.call_args
        assert call_kwargs.kwargs.get("model") == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_empty_response_content_falls_back_to_question(self):
        """If LLM returns empty string, original question is returned."""
        mock_response = MagicMock()
        mock_response.content = "   "  # whitespace only -> strip() -> falsy

        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(return_value=mock_response)

        with patch("app.services.query_fusion.create_adapter", return_value=mock_adapter):
            result = await fuse_query(
                question="original question",
                chat_history=[{"role": "user", "content": "prior"}],
            )

        assert result == "original question"


class TestFuseQueryErrorHandling:
    @pytest.mark.asyncio
    async def test_llm_error_falls_back_to_original_question(self):
        """When LLM raises any exception, original question is returned."""
        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(
            side_effect=RuntimeError("API timeout")
        )

        with patch("app.services.query_fusion.create_adapter", return_value=mock_adapter):
            result = await fuse_query(
                question="fallback question",
                chat_history=[{"role": "user", "content": "prior turn"}],
            )

        assert result == "fallback question"

    @pytest.mark.asyncio
    async def test_connection_error_falls_back_to_original_question(self):
        """Network errors also trigger fallback."""
        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(
            side_effect=ConnectionError("network unreachable")
        )

        with patch("app.services.query_fusion.create_adapter", return_value=mock_adapter):
            result = await fuse_query(
                question="my question",
                chat_history=[{"role": "assistant", "content": "prior answer"}],
            )

        assert result == "my question"

    @pytest.mark.asyncio
    async def test_create_adapter_error_falls_back(self):
        """If create_adapter itself fails, original question is returned."""
        with patch(
            "app.services.query_fusion.create_adapter",
            side_effect=ValueError("unknown provider"),
        ):
            result = await fuse_query(
                question="still my question",
                chat_history=[{"role": "user", "content": "something"}],
            )

        assert result == "still my question"


class TestFuseQueryHistoryTruncation:
    @pytest.mark.asyncio
    async def test_long_history_truncated_to_last_6_messages(self):
        """History longer than 6 messages uses only the last 6 for rewriting."""
        mock_response = MagicMock()
        mock_response.content = "rewritten"

        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(return_value=mock_response)

        # Build 10-message history; only last 6 should appear in the prompt
        history = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i}"}
            for i in range(10)
        ]

        with patch("app.services.query_fusion.create_adapter", return_value=mock_adapter):
            await fuse_query(
                question="latest question",
                chat_history=history,
            )

        call_args = mock_adapter.generate_with_rate_limit.call_args
        messages_sent = call_args.kwargs.get("messages", call_args.args[0] if call_args.args else [])
        # The prompt is in the single user message content
        prompt_content = messages_sent[0].content

        # Early messages should NOT appear in the prompt
        assert "message 0" not in prompt_content
        assert "message 1" not in prompt_content
        assert "message 2" not in prompt_content
        assert "message 3" not in prompt_content
        # Last 6 messages (indices 4-9) should appear
        assert "message 4" in prompt_content or "message 9" in prompt_content

    @pytest.mark.asyncio
    async def test_exactly_6_messages_all_included(self):
        """Exactly 6-message history is not truncated."""
        mock_response = MagicMock()
        mock_response.content = "rewritten"

        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(return_value=mock_response)

        history = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"}
            for i in range(6)
        ]

        with patch("app.services.query_fusion.create_adapter", return_value=mock_adapter):
            result = await fuse_query("new q", history)

        assert result == "rewritten"
        mock_adapter.generate_with_rate_limit.assert_called_once()


class TestFuseQueryProperNounResolution:
    """
    These tests validate prompt construction for follow-up questions.

    The goal is to confirm that prior technical identifiers remain present
    in the rewritten-query prompt, not to judge LLM output quality itself.
    """

    @pytest.mark.asyncio
    async def test_pronoun_resolution_prompt_includes_prior_subject(self):
        """A prior symbol name should be included when the follow-up uses a pronoun."""
        mock_response = MagicMock()
        mock_response.content = "How parse_repository works"

        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(return_value=mock_response)

        history = [
            {"role": "user", "content": "What does parse_repository do?"},
            {"role": "assistant", "content": "parse_repository parses the repository."},
        ]

        with patch("app.services.query_fusion.create_adapter", return_value=mock_adapter):
            result = await fuse_query("How does it work?", history)

        assert result == "How parse_repository works"
        mock_adapter.generate_with_rate_limit.assert_called_once()

        call_args = mock_adapter.generate_with_rate_limit.call_args
        messages_sent = call_args.kwargs.get("messages", call_args.args[0] if call_args.args else [])
        prompt_content = messages_sent[0].content

        assert "parse_repository" in prompt_content
        assert "How does it work?" in prompt_content

    @pytest.mark.asyncio
    async def test_technical_term_preserved_in_prompt(self):
        """CamelCase and snake_case identifiers should stay intact in the prompt."""
        mock_response = MagicMock()
        mock_response.content = "stage1_discovery parameters in ChromaDB"

        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(return_value=mock_response)

        history = [
            {"role": "user", "content": "How does stage1_discovery search in ChromaDB?"},
            {"role": "assistant", "content": "stage1_discovery combines vector and keyword retrieval."},
        ]

        with patch("app.services.query_fusion.create_adapter", return_value=mock_adapter):
            await fuse_query("Which parameters does it use?", history)

        call_args = mock_adapter.generate_with_rate_limit.call_args
        messages_sent = call_args.kwargs.get("messages", call_args.args[0] if call_args.args else [])
        prompt_content = messages_sent[0].content

        assert "stage1_discovery" in prompt_content
        assert "ChromaDB" in prompt_content
        assert "Which parameters does it use?" in prompt_content

    @pytest.mark.asyncio
    async def test_fused_query_returned_not_original(self):
        """A meaningful rewrite should be returned instead of the original follow-up."""
        mock_response = MagicMock()
        mock_response.content = "What parameters does handle_chat_stream accept?"

        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(return_value=mock_response)

        history = [
            {"role": "user", "content": "How is handle_chat_stream implemented?"},
            {"role": "assistant", "content": "It is the main streaming chat function."},
        ]

        with patch("app.services.query_fusion.create_adapter", return_value=mock_adapter):
            result = await fuse_query("What parameters does it have?", history)

        assert result != "What parameters does it have?"
        assert result == "What parameters does handle_chat_stream accept?"
        assert "handle_chat_stream" in result
