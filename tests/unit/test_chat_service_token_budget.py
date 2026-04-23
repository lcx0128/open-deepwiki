from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.mcp_types import CodeGuideline
from app.services import chat_service
from app.services.retrieval_planner import PlannedTarget


def _build_guidelines():
    return [
        CodeGuideline(
            chunk_id="chunk-high",
            name="high",
            file_path="app/high.py",
            node_type="function",
            start_line=1,
            end_line=10,
            description="high relevance",
            relevance_score=0.9,
        ),
        CodeGuideline(
            chunk_id="chunk-low",
            name="low",
            file_path="app/low.py",
            node_type="function",
            start_line=20,
            end_line=30,
            description="low relevance",
            relevance_score=0.4,
        ),
    ]


def _build_adapter():
    adapter = MagicMock()
    adapter.generate_with_rate_limit = AsyncMock(
        return_value=SimpleNamespace(content="final answer", usage={})
    )

    async def fake_stream_with_rate_limit(*args, **kwargs):
        for token in ("part-1", "part-2"):
            yield token

    adapter.stream_with_rate_limit = fake_stream_with_rate_limit
    return adapter


def _capture_budget_calls():
    captured = {}

    def fake_trim_chunks_to_budget(chunks, budget_tokens, weights=None):
        captured["chunks"] = list(chunks)
        captured["budget_tokens"] = budget_tokens
        captured["weights"] = list(weights or [])
        return ["trimmed-1", "trimmed-2"]

    def fake_apply_token_budget(messages, model, system_prompt, rag_context, user_query):
        captured["rag_context"] = rag_context
        captured["apply_user_query"] = user_query
        return [], rag_context

    def fake_compute_context_budget(model, system_prompt, user_query):
        captured["budget_user_query"] = user_query
        return 123

    return captured, fake_trim_chunks_to_budget, fake_apply_token_budget, fake_compute_context_budget


def _patch_common_dependencies(stack: ExitStack, captured: dict):
    adapter = _build_adapter()
    stack.enter_context(patch("app.services.chat_service.create_adapter", return_value=adapter))
    stack.enter_context(patch("app.services.chat_service.create_session", AsyncMock(return_value="session-1")))
    stack.enter_context(patch("app.services.chat_service.session_exists", AsyncMock(return_value=True)))
    stack.enter_context(patch("app.services.chat_service.get_history", AsyncMock(return_value=[])))
    stack.enter_context(patch("app.services.chat_service.append_turn", AsyncMock()))
    stack.enter_context(patch("app.services.chat_service.fuse_query", AsyncMock(return_value="fused query")))
    stack.enter_context(
        patch(
            "app.services.chat_service._three_way_retrieval",
            AsyncMock(return_value=(_build_guidelines(), ["base-high", "base-low"], [0.9, 0.4])),
        )
    )
    stack.enter_context(patch("app.services.chat_service.stage2_gap_fill_constants", AsyncMock(return_value=["gap-content"])))
    stack.enter_context(patch("app.services.chat_service._get_repo_name", AsyncMock(return_value="repo-name")))
    stack.enter_context(
        patch("app.services.chat_service._get_codebase_index", AsyncMock(return_value=("index text", {"app/main.py": {}})))
    )
    stack.enter_context(patch("app.services.chat_service._get_repo_dir", AsyncMock(return_value="E:/repo")))
    stack.enter_context(patch("app.services.chat_service.is_broad_query", return_value=True))
    stack.enter_context(
        patch(
            "app.services.chat_service.plan_retrieval",
            AsyncMock(return_value=[PlannedTarget(file_path="planned.py", symbol_name="target_symbol")]),
        )
    )
    stack.enter_context(patch("app.services.chat_service.read_targeted_context", AsyncMock(return_value="planned-content")))

    trim_mock = stack.enter_context(
        patch("app.services.chat_service.trim_chunks_to_budget", side_effect=captured["trim"])
    )
    apply_mock = stack.enter_context(
        patch("app.services.chat_service.apply_token_budget", side_effect=captured["apply"])
    )
    compute_mock = stack.enter_context(
        patch("app.services.chat_service._compute_context_budget", side_effect=captured["compute"])
    )
    return adapter, trim_mock, apply_mock, compute_mock


@pytest.mark.asyncio
async def test_handle_chat_trims_chunks_before_join_with_aligned_weights():
    captured, trim_fn, apply_fn, compute_fn = _capture_budget_calls()
    helpers = {"trim": trim_fn, "apply": apply_fn, "compute": compute_fn}

    with ExitStack() as stack:
        _patch_common_dependencies(stack, helpers)
        result = await chat_service.handle_chat(MagicMock(), "repo-1", "user query")

    assert captured["chunks"] == ["planned-content", "gap-content", "base-high", "base-low"]
    assert captured["weights"] == [0.5, 0.7, 0.9, 0.4]
    assert captured["budget_tokens"] == 123
    assert captured["budget_user_query"] == "user query"
    assert captured["rag_context"] == "trimmed-1\n\n---\n\ntrimmed-2"
    assert result["answer"] == "final answer"


@pytest.mark.asyncio
async def test_handle_chat_stream_trims_chunks_before_join_with_aligned_weights():
    captured, trim_fn, apply_fn, compute_fn = _capture_budget_calls()
    helpers = {"trim": trim_fn, "apply": apply_fn, "compute": compute_fn}

    with ExitStack() as stack:
        _patch_common_dependencies(stack, helpers)
        events = [
            event
            async for event in chat_service.handle_chat_stream(
                MagicMock(), "repo-1", "user query"
            )
        ]

    assert captured["chunks"] == ["planned-content", "gap-content", "base-high", "base-low"]
    assert captured["weights"] == [0.5, 0.7, 0.9, 0.4]
    assert captured["budget_tokens"] == 123
    assert captured["budget_user_query"] == "user query"
    assert captured["rag_context"] == "trimmed-1\n\n---\n\ntrimmed-2"
    assert events[-1] == {"type": "done"}


@pytest.mark.asyncio
async def test_handle_deep_research_stream_trims_chunks_before_join_with_aligned_weights():
    captured, trim_fn, apply_fn, compute_fn = _capture_budget_calls()
    helpers = {"trim": trim_fn, "apply": apply_fn, "compute": compute_fn}

    with ExitStack() as stack:
        _patch_common_dependencies(stack, helpers)
        events = [
            event
            async for event in chat_service.handle_deep_research_stream(
                MagicMock(),
                "repo-1",
                "user query",
                [{"role": "user", "content": "user query"}],
            )
        ]

    assert captured["chunks"] == ["planned-content", "gap-content", "base-high", "base-low"]
    assert captured["weights"] == [0.5, 0.7, 0.9, 0.4]
    assert captured["budget_tokens"] == 123
    assert "Research Plan" in captured["budget_user_query"]
    assert captured["rag_context"] == "trimmed-1\n\n---\n\ntrimmed-2"
    assert events[-1] == {"type": "done"}


@pytest.mark.asyncio
async def test_apply_evidence_check_degrades_when_supplemental_retrieval_fails():
    guidelines = _build_guidelines()
    code_contents = ["base-high", "base-low"]
    chunk_weights = [0.9, 0.4]
    insufficient_result = SimpleNamespace(
        is_sufficient=False,
        missing_aspects=["call_chain"],
        suggested_queries=["handle_chat_stream"],
    )

    with (
        patch(
            "app.services.chat_service.check_evidence_sufficiency",
            return_value=insufficient_result,
        ),
        patch(
            "app.services.chat_service._run_supplemental_retrieval",
            AsyncMock(side_effect=RuntimeError("boom")),
        ),
    ):
        result = await chat_service._apply_evidence_check(
            "complex query",
            "repo-1",
            guidelines,
            code_contents,
            chunk_weights,
            index_data={"app/main.py": {}},
            repo_dir="E:/repo",
        )

    assert result == (guidelines, code_contents, chunk_weights)


def test_infer_expansion_direction_prefers_caller_for_upstream_queries():
    assert (
        chat_service._infer_expansion_direction(["call_chain"], "stage1_discovery 谁调用")
        == "caller"
    )


def test_infer_expansion_direction_defaults_to_callee_for_implementation_queries():
    assert (
        chat_service._infer_expansion_direction(["call_chain", "implementation"], "实现流程")
        == "callee"
    )


@pytest.mark.asyncio
async def test_supplemental_retrieval_runs_dependency_expansion_without_queries():
    guidelines = _build_guidelines()
    expanded = CodeGuideline(
        chunk_id="dep-chunk",
        name="stage2_assembly",
        file_path="app/services/two_stage_retriever.py",
        node_type="function_definition",
        start_line=40,
        end_line=80,
        description="[dep-graph:callee] async def stage2_assembly",
        relevance_score=0.65,
        source="dep_graph",
    )

    with (
        patch(
            "app.services.two_stage_retriever.expand_via_dependency_graph",
            AsyncMock(return_value=[expanded]),
        ) as expand_mock,
        patch("app.services.chat_service.stage2_assembly", AsyncMock(return_value=["dep content"])),
    ):
        result_guidelines, result_contents, result_weights = await chat_service._run_supplemental_retrieval(
            missing_aspects=["call_chain"],
            suggested_queries=[],
            repo_id="repo-1",
            existing_guidelines=guidelines,
            existing_contents=["base-high", "base-low"],
            existing_weights=[0.9, 0.4],
            fused_query="完整调用链",
        )

    assert result_guidelines == guidelines + [expanded]
    assert result_contents == ["base-high", "base-low", "dep content"]
    assert result_weights == [0.9, 0.4, 0.65]
    expand_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_supplemental_retrieval_passes_caller_direction_to_dependency_expansion():
    with (
        patch(
            "app.services.two_stage_retriever.expand_via_dependency_graph",
            AsyncMock(return_value=[]),
        ) as expand_mock,
        patch("app.services.chat_service.stage2_assembly", AsyncMock()),
    ):
        await chat_service._run_supplemental_retrieval(
            missing_aspects=["call_chain"],
            suggested_queries=[],
            repo_id="repo-1",
            existing_guidelines=_build_guidelines(),
            existing_contents=["base-high", "base-low"],
            existing_weights=[0.9, 0.4],
            fused_query="high 被哪里调用",
        )

    assert expand_mock.await_args.kwargs["direction"] == "caller"
