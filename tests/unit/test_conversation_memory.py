# tests/unit/test_conversation_memory.py
"""
Unit tests for app/services/conversation_memory.py

Covers:
- create_session: returns valid UUID, calls redis.hset and redis.expire
- get_history: empty list when no data, parsed messages when data exists
- append_turn: adds user+assistant messages, updates total_tokens, refreshes TTL
- session_exists: True when key exists, False when not
"""
import json
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.conversation_memory import (
    SESSION_TTL,
    create_session,
    get_history,
    append_turn,
    session_exists,
)


def _make_redis_mock(**overrides) -> AsyncMock:
    """Build a minimal Redis AsyncMock with sensible defaults."""
    mock = AsyncMock()
    mock.hset = AsyncMock(return_value=1)
    mock.expire = AsyncMock(return_value=True)
    mock.hget = AsyncMock(return_value=None)
    mock.exists = AsyncMock(return_value=0)
    for attr, val in overrides.items():
        setattr(mock, attr, val)
    return mock


class TestCreateSession:
    @pytest.mark.asyncio
    async def test_returns_valid_uuid_string(self):
        """create_session returns a non-empty UUID-formatted string."""
        redis_mock = _make_redis_mock()
        with patch("app.services.conversation_memory.get_redis", AsyncMock(return_value=redis_mock)):
            session_id = await create_session("repo-123")

        assert isinstance(session_id, str)
        assert len(session_id) > 0
        # Must be a valid UUID
        parsed = uuid.UUID(session_id)
        assert str(parsed) == session_id

    @pytest.mark.asyncio
    async def test_calls_hset_with_correct_key(self):
        """create_session calls redis.hset with key 'conversation:{session_id}'."""
        redis_mock = _make_redis_mock()
        with patch("app.services.conversation_memory.get_redis", AsyncMock(return_value=redis_mock)):
            session_id = await create_session("repo-abc")

        redis_mock.hset.assert_called_once()
        call_args = redis_mock.hset.call_args
        key_used = call_args.args[0] if call_args.args else call_args.kwargs.get("name", "")
        assert key_used == f"conversation:{session_id}"

    @pytest.mark.asyncio
    async def test_hset_stores_repo_id(self):
        """create_session stores the repo_id in the hash."""
        redis_mock = _make_redis_mock()
        with patch("app.services.conversation_memory.get_redis", AsyncMock(return_value=redis_mock)):
            await create_session("my-repo-id")

        call_args = redis_mock.hset.call_args
        mapping = call_args.kwargs.get("mapping", {})
        assert mapping.get("repo_id") == "my-repo-id"

    @pytest.mark.asyncio
    async def test_hset_stores_empty_messages_list(self):
        """create_session stores an empty JSON array as messages."""
        redis_mock = _make_redis_mock()
        with patch("app.services.conversation_memory.get_redis", AsyncMock(return_value=redis_mock)):
            await create_session("repo-x")

        call_args = redis_mock.hset.call_args
        mapping = call_args.kwargs.get("mapping", {})
        messages = json.loads(mapping.get("messages", "null"))
        assert messages == []

    @pytest.mark.asyncio
    async def test_calls_expire_with_session_ttl(self):
        """create_session calls redis.expire with SESSION_TTL."""
        redis_mock = _make_redis_mock()
        with patch("app.services.conversation_memory.get_redis", AsyncMock(return_value=redis_mock)):
            session_id = await create_session("repo-y")

        redis_mock.expire.assert_called_once_with(
            f"conversation:{session_id}", SESSION_TTL
        )

    @pytest.mark.asyncio
    async def test_session_ttl_is_24_hours(self):
        """SESSION_TTL constant equals 86400 seconds (24 hours)."""
        assert SESSION_TTL == 86400

    @pytest.mark.asyncio
    async def test_each_call_returns_unique_session_id(self):
        """Two consecutive calls return different session IDs."""
        redis_mock = _make_redis_mock()
        with patch("app.services.conversation_memory.get_redis", AsyncMock(return_value=redis_mock)):
            s1 = await create_session("repo-1")
            s2 = await create_session("repo-1")

        assert s1 != s2


class TestGetHistory:
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_data(self):
        """get_history returns [] when redis.hget returns None."""
        redis_mock = _make_redis_mock(hget=AsyncMock(return_value=None))
        with patch("app.services.conversation_memory.get_redis", AsyncMock(return_value=redis_mock)):
            history = await get_history("nonexistent-session")

        assert history == []

    @pytest.mark.asyncio
    async def test_returns_parsed_messages_when_data_exists(self):
        """get_history parses JSON stored in redis and returns the list."""
        stored_messages = [
            {"id": "aaa", "role": "user", "content": "hello"},
            {"id": "bbb", "role": "assistant", "content": "world"},
        ]
        redis_mock = _make_redis_mock(
            hget=AsyncMock(return_value=json.dumps(stored_messages))
        )
        with patch("app.services.conversation_memory.get_redis", AsyncMock(return_value=redis_mock)):
            history = await get_history("some-session-id")

        assert history == stored_messages

    @pytest.mark.asyncio
    async def test_queries_correct_key(self):
        """get_history reads from 'conversation:{session_id}' key."""
        redis_mock = _make_redis_mock(hget=AsyncMock(return_value=None))
        with patch("app.services.conversation_memory.get_redis", AsyncMock(return_value=redis_mock)):
            await get_history("test-session-42")

        redis_mock.hget.assert_called_once_with(
            "conversation:test-session-42", "messages"
        )

    @pytest.mark.asyncio
    async def test_returns_list_type(self):
        """Return value is always a list."""
        redis_mock = _make_redis_mock(hget=AsyncMock(return_value=None))
        with patch("app.services.conversation_memory.get_redis", AsyncMock(return_value=redis_mock)):
            result = await get_history("sid")

        assert isinstance(result, list)


class TestAppendTurn:
    @pytest.mark.asyncio
    async def test_adds_user_and_assistant_messages(self):
        """append_turn writes two new messages (user + assistant) to the history."""
        initial_messages: list = []
        captured_mapping: dict = {}

        async def fake_hget(key, field):
            if field == "messages":
                return json.dumps(initial_messages)
            if field == "total_tokens":
                return "0"
            return None

        async def fake_hset(key, mapping):
            captured_mapping.update(mapping)

        redis_mock = _make_redis_mock(
            hget=AsyncMock(side_effect=fake_hget),
            hset=AsyncMock(side_effect=fake_hset),
        )

        with patch("app.services.conversation_memory.get_redis", AsyncMock(return_value=redis_mock)):
            await append_turn(
                session_id="sess-1",
                user_query="What is this?",
                assistant_response="It is a test.",
            )

        written_messages = json.loads(captured_mapping["messages"])
        assert len(written_messages) == 2
        assert written_messages[0]["role"] == "user"
        assert written_messages[0]["content"] == "What is this?"
        assert written_messages[1]["role"] == "assistant"
        assert written_messages[1]["content"] == "It is a test."

    @pytest.mark.asyncio
    async def test_appends_to_existing_messages(self):
        """append_turn preserves existing messages and appends new ones."""
        existing = [
            {"id": "old-1", "role": "user", "content": "prior question"},
            {"id": "old-2", "role": "assistant", "content": "prior answer"},
        ]
        captured_mapping: dict = {}

        async def fake_hget(key, field):
            if field == "messages":
                return json.dumps(existing)
            if field == "total_tokens":
                return "50"
            return None

        async def fake_hset(key, mapping):
            captured_mapping.update(mapping)

        redis_mock = _make_redis_mock(
            hget=AsyncMock(side_effect=fake_hget),
            hset=AsyncMock(side_effect=fake_hset),
        )

        with patch("app.services.conversation_memory.get_redis", AsyncMock(return_value=redis_mock)):
            await append_turn("sess-2", "new q", "new a", tokens_used=30)

        written = json.loads(captured_mapping["messages"])
        assert len(written) == 4  # 2 existing + 2 new
        assert written[0]["content"] == "prior question"
        assert written[2]["content"] == "new q"

    @pytest.mark.asyncio
    async def test_updates_total_tokens(self):
        """append_turn accumulates tokens_used into total_tokens."""
        captured_mapping: dict = {}

        async def fake_hget(key, field):
            if field == "messages":
                return json.dumps([])
            if field == "total_tokens":
                return "100"
            return None

        async def fake_hset(key, mapping):
            captured_mapping.update(mapping)

        redis_mock = _make_redis_mock(
            hget=AsyncMock(side_effect=fake_hget),
            hset=AsyncMock(side_effect=fake_hset),
        )

        with patch("app.services.conversation_memory.get_redis", AsyncMock(return_value=redis_mock)):
            await append_turn("sess-3", "q", "a", tokens_used=42)

        assert captured_mapping["total_tokens"] == "142"

    @pytest.mark.asyncio
    async def test_calls_expire_to_refresh_ttl(self):
        """append_turn calls redis.expire to reset the session TTL."""
        async def fake_hget(key, field):
            if field == "messages":
                return json.dumps([])
            return "0"

        redis_mock = _make_redis_mock(hget=AsyncMock(side_effect=fake_hget))

        with patch("app.services.conversation_memory.get_redis", AsyncMock(return_value=redis_mock)):
            await append_turn("sess-4", "q", "a")

        redis_mock.expire.assert_called_once_with(
            "conversation:sess-4", SESSION_TTL
        )

    @pytest.mark.asyncio
    async def test_stores_chunk_refs_in_assistant_message(self):
        """chunk_refs are stored in the assistant's message entry."""
        captured_mapping: dict = {}

        async def fake_hget(key, field):
            if field == "messages":
                return json.dumps([])
            return "0"

        async def fake_hset(key, mapping):
            captured_mapping.update(mapping)

        redis_mock = _make_redis_mock(
            hget=AsyncMock(side_effect=fake_hget),
            hset=AsyncMock(side_effect=fake_hset),
        )
        refs = [{"file_path": "app/main.py", "start_line": 1, "end_line": 10, "name": "main"}]

        with patch("app.services.conversation_memory.get_redis", AsyncMock(return_value=redis_mock)):
            await append_turn("sess-5", "q", "a", chunk_refs=refs)

        written = json.loads(captured_mapping["messages"])
        assistant_msg = written[1]
        assert assistant_msg["chunk_refs"] == refs

    @pytest.mark.asyncio
    async def test_no_chunk_refs_defaults_to_empty_list(self):
        """When chunk_refs is None, assistant message stores empty list."""
        captured_mapping: dict = {}

        async def fake_hget(key, field):
            if field == "messages":
                return json.dumps([])
            return "0"

        async def fake_hset(key, mapping):
            captured_mapping.update(mapping)

        redis_mock = _make_redis_mock(
            hget=AsyncMock(side_effect=fake_hget),
            hset=AsyncMock(side_effect=fake_hset),
        )

        with patch("app.services.conversation_memory.get_redis", AsyncMock(return_value=redis_mock)):
            await append_turn("sess-6", "q", "a", chunk_refs=None)

        written = json.loads(captured_mapping["messages"])
        assert written[1]["chunk_refs"] == []


class TestSessionExists:
    @pytest.mark.asyncio
    async def test_returns_true_when_key_exists(self):
        """session_exists returns True when redis.exists returns non-zero."""
        redis_mock = _make_redis_mock(exists=AsyncMock(return_value=1))
        with patch("app.services.conversation_memory.get_redis", AsyncMock(return_value=redis_mock)):
            result = await session_exists("existing-session")

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_key_absent(self):
        """session_exists returns False when redis.exists returns 0."""
        redis_mock = _make_redis_mock(exists=AsyncMock(return_value=0))
        with patch("app.services.conversation_memory.get_redis", AsyncMock(return_value=redis_mock)):
            result = await session_exists("missing-session")

        assert result is False

    @pytest.mark.asyncio
    async def test_checks_correct_key(self):
        """session_exists calls redis.exists with the correct key format."""
        redis_mock = _make_redis_mock(exists=AsyncMock(return_value=0))
        with patch("app.services.conversation_memory.get_redis", AsyncMock(return_value=redis_mock)):
            await session_exists("my-session-id")

        redis_mock.exists.assert_called_once_with("conversation:my-session-id")
