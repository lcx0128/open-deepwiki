import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.mcp_types import CodeGuideline


def _fake_chat_result(
    session_id: str = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    answer: str = "The parse_repository function clones and parses a Git repo.",
):
    return {
        "session_id": session_id,
        "answer": answer,
        "chunk_refs": [
            {
                "file_path": "app/services/parser.py",
                "start_line": 10,
                "end_line": 40,
                "name": "parse_repository",
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 80},
    }


def _parse_sse_payloads(raw_text: str) -> list[dict]:
    payloads = []
    for line in raw_text.splitlines():
        if not line.startswith("data: "):
            continue
        payloads.append(json.loads(line[6:]))
    return payloads


@pytest.mark.asyncio
async def test_post_chat_returns_200_with_valid_payload(client):
    fake_result = _fake_chat_result()

    with patch(
        "app.api.chat.handle_chat",
        new=AsyncMock(return_value=fake_result),
    ) as mock_handle_chat:
        response = await client.post(
            "/api/chat",
            json={
                "repo_id": "repo-uuid-123",
                "query": "What does parse_repository do?",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == fake_result["session_id"]
    assert data["answer"] == fake_result["answer"]
    assert data["chunk_refs"] == fake_result["chunk_refs"]
    assert data["usage"] == fake_result["usage"]
    mock_handle_chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_chat_with_existing_session_id(client):
    existing_session = "11111111-2222-3333-4444-555555555555"
    fake_result = _fake_chat_result(
        session_id=existing_session,
        answer="Continuing the conversation.",
    )

    with patch(
        "app.api.chat.handle_chat",
        new=AsyncMock(return_value=fake_result),
    ):
        response = await client.post(
            "/api/chat",
            json={
                "repo_id": "repo-uuid-123",
                "session_id": existing_session,
                "query": "Tell me more.",
            },
        )

    assert response.status_code == 200
    assert response.json()["session_id"] == existing_session


@pytest.mark.asyncio
async def test_post_chat_missing_repo_id_returns_422(client):
    response = await client.post(
        "/api/chat",
        json={"query": "What does this do?"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert "repo_id" in [err["loc"][-1] for err in detail]


@pytest.mark.asyncio
async def test_post_chat_missing_query_returns_422(client):
    response = await client.post(
        "/api/chat",
        json={"repo_id": "repo-uuid-123"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "query" in [err["loc"][-1] for err in detail]


@pytest.mark.asyncio
async def test_post_chat_empty_body_returns_422(client):
    response = await client.post("/api/chat", json={})

    assert response.status_code == 422
    detail = response.json()["detail"]
    missing_fields = [err["loc"][-1] for err in detail]
    assert "repo_id" in missing_fields
    assert "query" in missing_fields


@pytest.mark.asyncio
async def test_get_chat_stream_returns_event_stream_content_type(client):
    async def fake_stream(*args, **kwargs):
        yield {"type": "session_id", "session_id": "new-session-id"}
        yield {"type": "token", "content": "Hello"}
        yield {"type": "done"}

    with patch(
        "app.api.chat.handle_chat_stream",
        new=MagicMock(return_value=fake_stream()),
    ):
        response = await client.get(
            "/api/chat/stream",
            params={
                "repo_id": "repo-uuid-123",
                "query": "Explain the architecture",
            },
        )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    payloads = _parse_sse_payloads(response.text)
    assert [payload["type"] for payload in payloads] == ["session_id", "token", "done"]


@pytest.mark.asyncio
async def test_get_chat_stream_missing_repo_id_returns_422(client):
    response = await client.get(
        "/api/chat/stream",
        params={"query": "some question"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "repo_id" in [err["loc"][-1] for err in detail]


@pytest.mark.asyncio
async def test_get_chat_stream_missing_query_returns_422(client):
    response = await client.get(
        "/api/chat/stream",
        params={"repo_id": "repo-uuid-123"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "query" in [err["loc"][-1] for err in detail]


@pytest.mark.asyncio
async def test_post_chat_service_404_returns_http_404(client):
    with patch(
        "app.api.chat.handle_chat",
        new=AsyncMock(side_effect=FileNotFoundError("Repository not indexed")),
    ):
        response = await client.post(
            "/api/chat",
            json={
                "repo_id": "nonexistent-repo",
                "query": "Does this exist?",
            },
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Repository not indexed"


@pytest.mark.asyncio
async def test_post_chat_service_value_error_returns_http_400(client):
    with patch(
        "app.api.chat.handle_chat",
        new=AsyncMock(side_effect=ValueError("invalid session_id format")),
    ):
        response = await client.post(
            "/api/chat",
            json={
                "repo_id": "repo-uuid-123",
                "query": "A question",
                "session_id": "bad-session",
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid session_id format"


class TestSSEEventOrder:
    """Directly exercise handle_chat_stream without app-level transport."""

    @staticmethod
    async def _collect_stream_events():
        from app.services.chat_service import handle_chat_stream

        mock_db = AsyncMock()
        guidelines = [
            CodeGuideline(
                chunk_id="chunk-1",
                name="parse_repository",
                file_path="app/services/parser.py",
                node_type="function",
                start_line=10,
                end_line=40,
                description="Parse repository entry point",
                relevance_score=0.98,
            )
        ]

        async def fake_stream_with_rate_limit(**kwargs):
            yield "Hello"
            yield " World"

        mock_adapter = MagicMock()
        mock_adapter.stream_with_rate_limit = MagicMock(
            return_value=fake_stream_with_rate_limit()
        )

        with patch(
            "app.services.chat_service.fuse_query",
            new=AsyncMock(return_value="fused query"),
        ), patch(
            "app.services.chat_service._three_way_retrieval",
            new=AsyncMock(return_value=(guidelines, ["chunk_1"], [0.98])),
        ), patch(
            "app.services.chat_service.stage2_gap_fill_constants",
            new=AsyncMock(return_value=[]),
        ), patch(
            "app.services.chat_service._get_codebase_index",
            new=AsyncMock(return_value=(None, None)),
        ), patch(
            "app.services.chat_service._get_repo_dir",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.services.chat_service._get_repo_name",
            new=AsyncMock(return_value="test-repo"),
        ), patch(
            "app.services.chat_service.create_session",
            new=AsyncMock(return_value="test-session-id"),
        ), patch(
            "app.services.chat_service.session_exists",
            new=AsyncMock(return_value=True),
        ), patch(
            "app.services.chat_service.get_history",
            new=AsyncMock(return_value=[]),
        ), patch(
            "app.services.chat_service.append_turn",
            new=AsyncMock(),
        ), patch(
            "app.services.chat_service.is_broad_query",
            return_value=False,
        ), patch(
            "app.services.chat_service.create_adapter",
            return_value=mock_adapter,
        ):
            events = []
            async for event in handle_chat_stream(
                db=mock_db,
                repo_id="test-repo-id",
                query="test query",
                session_id=None,
                llm_model="gpt-4o-mini",
            ):
                events.append(event)

        return events

    @pytest.mark.asyncio
    async def test_sse_event_order_session_id_first_done_last(self):
        events = await self._collect_stream_events()
        event_types = [event["type"] for event in events]

        assert event_types == ["session_id", "token", "token", "chunk_refs", "done"]

    @pytest.mark.asyncio
    async def test_sse_token_events_between_session_and_done(self):
        events = await self._collect_stream_events()
        event_types = [event["type"] for event in events]

        session_idx = event_types.index("session_id")
        done_idx = event_types.index("done")
        chunk_refs_idx = event_types.index("chunk_refs")
        token_indices = [
            idx for idx, event_type in enumerate(event_types) if event_type == "token"
        ]

        assert token_indices
        assert session_idx == 0
        assert all(session_idx < idx < done_idx for idx in token_indices)
        assert max(token_indices) < chunk_refs_idx
        assert session_idx < chunk_refs_idx < done_idx
