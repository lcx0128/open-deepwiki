# tests/integration/test_chat_api.py
"""
Integration test skeletons for the chat API endpoints.

All tests are marked skip because they require running services
(PostgreSQL/SQLite, Redis, ChromaDB, and a live LLM or mock).

To run without live services, the individual tests below show exactly
which patch targets to mock: app.services.chat_service.handle_chat
and app.services.chat_service.handle_chat_stream.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# ---------------------------------------------------------------------------
# Lazy import guard: only import app.main inside tests so that missing .env
# or Redis config does not break the collection phase.
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="requires running services")
def test_post_chat_returns_200_with_valid_payload():
    """
    POST /api/chat with a valid body and mocked chat_service returns HTTP 200
    and a ChatResponse-shaped JSON body.

    Expected response shape:
        {
            "session_id": "<uuid>",
            "answer": "<string>",
            "chunk_refs": [...],
            "usage": {...} | null
        }
    """
    from fastapi.testclient import TestClient
    from app.main import app

    fake_result = {
        "session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "answer": "The parse_repository function clones and parses a Git repo.",
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

    with patch(
        "app.services.chat_service.handle_chat",
        new=AsyncMock(return_value=fake_result),
    ):
        with TestClient(app) as client:
            response = client.post(
                "/api/chat",
                json={
                    "repo_id": "repo-uuid-123",
                    "query": "What does parse_repository do?",
                },
            )

    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data
    assert "answer" in data
    assert isinstance(data["chunk_refs"], list)


@pytest.mark.skip(reason="requires running services")
def test_post_chat_with_existing_session_id():
    """
    POST /api/chat with an existing session_id continues the conversation.
    The session_id in the response should match the one sent.
    """
    from fastapi.testclient import TestClient
    from app.main import app

    existing_session = "11111111-2222-3333-4444-555555555555"
    fake_result = {
        "session_id": existing_session,
        "answer": "Continuing the conversation.",
        "chunk_refs": [],
        "usage": None,
    }

    with patch(
        "app.services.chat_service.handle_chat",
        new=AsyncMock(return_value=fake_result),
    ):
        with TestClient(app) as client:
            response = client.post(
                "/api/chat",
                json={
                    "repo_id": "repo-uuid-123",
                    "session_id": existing_session,
                    "query": "Tell me more.",
                },
            )

    assert response.status_code == 200
    assert response.json()["session_id"] == existing_session


@pytest.mark.skip(reason="requires running services")
def test_post_chat_missing_repo_id_returns_422():
    """
    POST /api/chat without the required repo_id field returns HTTP 422
    (Unprocessable Entity) from FastAPI's request validation.
    """
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={
                # repo_id intentionally omitted
                "query": "What does this do?",
            },
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    # FastAPI returns a list of validation errors
    assert isinstance(detail, list)
    field_names = [err["loc"][-1] for err in detail]
    assert "repo_id" in field_names


@pytest.mark.skip(reason="requires running services")
def test_post_chat_missing_query_returns_422():
    """
    POST /api/chat without the required query field returns HTTP 422.
    """
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={"repo_id": "repo-uuid-123"},
        )

    assert response.status_code == 422


@pytest.mark.skip(reason="requires running services")
def test_post_chat_empty_body_returns_422():
    """
    POST /api/chat with an empty JSON body returns HTTP 422.
    """
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        response = client.post("/api/chat", json={})

    assert response.status_code == 422


@pytest.mark.skip(reason="requires running services")
def test_get_chat_stream_returns_event_stream_content_type():
    """
    GET /api/chat/stream with valid query params returns:
    - HTTP 200
    - Content-Type: text/event-stream
    """
    from fastapi.testclient import TestClient
    from app.main import app

    async def fake_stream(*args, **kwargs):
        yield {"type": "session_id", "session_id": "new-session-id"}
        yield {"type": "token", "content": "Hello"}
        yield {"type": "done"}

    with patch(
        "app.services.chat_service.handle_chat_stream",
        new=MagicMock(return_value=fake_stream()),
    ):
        with TestClient(app) as client:
            response = client.get(
                "/api/chat/stream",
                params={
                    "repo_id": "repo-uuid-123",
                    "query": "Explain the architecture",
                },
            )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")


@pytest.mark.skip(reason="requires running services")
def test_get_chat_stream_missing_repo_id_returns_422():
    """
    GET /api/chat/stream without the required repo_id query parameter
    returns HTTP 422.
    """
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        response = client.get(
            "/api/chat/stream",
            params={"query": "some question"},
        )

    assert response.status_code == 422


@pytest.mark.skip(reason="requires running services")
def test_get_chat_stream_missing_query_returns_422():
    """
    GET /api/chat/stream without the required query parameter returns HTTP 422.
    """
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        response = client.get(
            "/api/chat/stream",
            params={"repo_id": "repo-uuid-123"},
        )

    assert response.status_code == 422


@pytest.mark.skip(reason="requires running services")
def test_post_chat_service_404_returns_http_404():
    """
    When handle_chat raises FileNotFoundError (e.g. repo not in vector store),
    the API returns HTTP 404.
    """
    from fastapi.testclient import TestClient
    from app.main import app

    with patch(
        "app.services.chat_service.handle_chat",
        new=AsyncMock(side_effect=FileNotFoundError("Repository not indexed")),
    ):
        with TestClient(app) as client:
            response = client.post(
                "/api/chat",
                json={
                    "repo_id": "nonexistent-repo",
                    "query": "Does this exist?",
                },
            )

    assert response.status_code == 404


@pytest.mark.skip(reason="requires running services")
def test_post_chat_service_value_error_returns_http_400():
    """
    When handle_chat raises ValueError (e.g. invalid session_id),
    the API returns HTTP 400.
    """
    from fastapi.testclient import TestClient
    from app.main import app

    with patch(
        "app.services.chat_service.handle_chat",
        new=AsyncMock(side_effect=ValueError("invalid session_id format")),
    ):
        with TestClient(app) as client:
            response = client.post(
                "/api/chat",
                json={
                    "repo_id": "repo-uuid-123",
                    "query": "A question",
                    "session_id": "bad-session",
                },
            )

    assert response.status_code == 400
