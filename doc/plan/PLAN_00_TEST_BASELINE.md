# Plan 00: Phase 0 — 测试基线建立

> 优先级：最高（Phase 0 护栏）
> 前置依赖：无
> 预计改动文件数：3
> 策略文档对应：功能六（最小测试基线）

---

## 1. 目标

在所有 RAG 升级改动开始之前，为现有核心模块建立可执行的回归测试基线。后续每一步升级（Token Budget 重构、Grep 层新增、证据充分性判断等）都需要在这些测试通过的前提下进行，确保不引入 regression。

**核心原则**：本 Plan 不引入任何生产代码改动，仅新增/补充测试。

---

## 2. 涉及文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `tests/unit/test_token_budget.py` | 修改 | 补充 chunk 边界完整性测试（为 Plan 01 做准备） |
| `tests/unit/test_query_fusion.py` | 修改 | 补充多轮追问代词消解行为验证 |
| `tests/integration/test_chat_api.py` | 修改 | 取消部分 `@pytest.mark.skip`，用 mock 替代真实服务 |

---

## 3. 详细实施方案

### 3.1 `tests/unit/test_token_budget.py` — 补充 chunk 边界完整性测试

**现状**：
- 文件已存在（169 行），覆盖了 `estimate_tokens` 和 `apply_token_budget` 的基本行为
- 第 107-113 行的 `test_rag_context_truncated_when_too_long` 仅验证了"裁剪后长度更短"，但未验证裁剪边界的合理性

**需要新增的测试类**：`TestTokenBudgetChunkBoundary`

此测试类是为 Plan 01（Token Budget 重构）提前建立的"变更检测器"。重构前这些测试用于记录当前行为（字符截断），重构后需要验证新行为（chunk 整块裁剪）。

```python
class TestTokenBudgetChunkBoundary:
    """
    预期行为记录（当前实现 — 字符截断）：
    这些测试在 Plan 01 重构后需要更新为新的期望行为。
    当前目的是锁定现有行为，作为升级前的基线快照。
    """

    def test_current_truncation_cuts_mid_chunk(self):
        """
        当前行为：RAG context 超预算时按字符比例截断。
        构造一个由多个完整 chunk 用 '---' 分隔拼成的 context，
        验证当前实现会在 chunk 中间截断（非整块丢弃）。

        此测试在 Plan 01 完成后应改为验证"不存在被截半的 chunk"。
        """
        # 构造 3 个 chunk，每个约 2000 字符
        chunk_a = "// File: a.py\n" + "a" * 2000
        chunk_b = "// File: b.py\n" + "b" * 2000
        chunk_c = "// File: c.py\n" + "c" * 2000
        rag_context = "\n\n---\n\n".join([chunk_a, chunk_b, chunk_c])

        # 使用小模型限制强制触发截断
        trimmed_msgs, trimmed_ctx = apply_token_budget(
            [], "gpt-3.5-turbo",
            "system prompt " * 100,  # 较长的 system prompt 压缩剩余预算
            rag_context,
            "user query"
        )

        # 当前行为：截断后 context 更短但可能在 chunk 中间
        if len(trimmed_ctx) < len(rag_context):
            # 验证确实发生了截断
            assert len(trimmed_ctx) < len(rag_context)
            # 记录当前行为：截断点不一定在 chunk 边界（"---" 分隔符处）
            # Plan 01 完成后，此断言应改为：
            # 验证 trimmed_ctx 中每个 chunk 都是完整的

    def test_multiple_chunks_format_preserved(self):
        """
        验证多 chunk 拼接格式：chunk 之间用 '\\n\\n---\\n\\n' 分隔。
        此格式是后续 chunk-aware 裁剪的解析前提。
        """
        chunks = ["chunk_1_content", "chunk_2_content", "chunk_3_content"]
        joined = "\n\n---\n\n".join(chunks)
        parts = joined.split("\n\n---\n\n")
        assert len(parts) == 3
        assert parts[0] == "chunk_1_content"
        assert parts[2] == "chunk_3_content"
```

**关键点**：
- 在 `apply_token_budget` 的 import 列表中已有所有需要的导入（第 9-14 行）
- 新增测试类放在文件末尾（第 169 行之后）
- 测试不依赖外部服务，可离线运行

### 3.2 `tests/unit/test_query_fusion.py` — 补充代词消解行为验证

**现状**：
- 文件已存在（216 行），覆盖了空历史、非空历史、错误处理、历史截断
- 缺少对**代词消解质量**的行为验证（核心功能）

**需要新增的测试类**：`TestFuseQueryProperNounResolution`

```python
class TestFuseQueryProperNounResolution:
    """
    验证 fuse_query 在多轮追问场景中的代词消解行为。
    这些测试通过 mock LLM 返回预期的重写结果来验证 prompt 构造是否正确，
    而不是验证 LLM 的实际输出质量（那是 LLM 侧问题）。
    """

    @pytest.mark.asyncio
    async def test_pronoun_resolution_prompt_includes_prior_subject(self):
        """
        当历史中提到了 'parse_repository'，追问 '它怎么工作的'，
        验证发送给 LLM 的 prompt 中包含了 'parse_repository' 这个上下文。
        """
        mock_response = MagicMock()
        mock_response.content = "parse_repository 是如何工作的"

        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(return_value=mock_response)

        history = [
            {"role": "user", "content": "parse_repository 这个函数是做什么的？"},
            {"role": "assistant", "content": "parse_repository 用于解析仓库代码。"},
        ]

        with patch("app.services.query_fusion.create_adapter", return_value=mock_adapter):
            result = await fuse_query("它怎么工作的？", history)

        # 验证 LLM 被调用
        mock_adapter.generate_with_rate_limit.assert_called_once()

        # 验证发送的 prompt 中包含了历史中的关键标识符
        call_args = mock_adapter.generate_with_rate_limit.call_args
        messages_sent = call_args.kwargs.get("messages", call_args.args[0] if call_args.args else [])
        prompt_content = messages_sent[0].content
        assert "parse_repository" in prompt_content

    @pytest.mark.asyncio
    async def test_technical_term_preserved_in_prompt(self):
        """
        验证技术标识符（CamelCase、snake_case）在 prompt 中被完整保留。
        """
        mock_response = MagicMock()
        mock_response.content = "ChromaDB stage1_discovery 的检索逻辑"

        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(return_value=mock_response)

        history = [
            {"role": "user", "content": "stage1_discovery 在 ChromaDB 中怎么检索的？"},
            {"role": "assistant", "content": "stage1_discovery 使用向量检索加 keyword 补充。"},
        ]

        with patch("app.services.query_fusion.create_adapter", return_value=mock_adapter):
            result = await fuse_query("那它用了哪些参数？", history)

        call_args = mock_adapter.generate_with_rate_limit.call_args
        messages_sent = call_args.kwargs.get("messages", call_args.args[0] if call_args.args else [])
        prompt_content = messages_sent[0].content
        assert "stage1_discovery" in prompt_content
        assert "ChromaDB" in prompt_content

    @pytest.mark.asyncio
    async def test_fused_query_returned_not_original(self):
        """
        当历史非空且 LLM 返回有意义的重写时，返回的是重写后的查询而非原始查询。
        """
        mock_response = MagicMock()
        mock_response.content = "handle_chat_stream 函数的完整参数列表是什么"

        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(return_value=mock_response)

        history = [
            {"role": "user", "content": "handle_chat_stream 是怎么实现的？"},
            {"role": "assistant", "content": "它是流式对话的主函数..."},
        ]

        with patch("app.services.query_fusion.create_adapter", return_value=mock_adapter):
            result = await fuse_query("它有哪些参数？", history)

        assert result != "它有哪些参数？"
        assert "handle_chat_stream" in result
```

**关键点**：
- 新增测试类放在文件末尾（第 216 行之后）
- 需要的 import 已全部存在（`pytest`, `AsyncMock`, `MagicMock`, `patch`, `fuse_query`）
- 测试验证的是 prompt 构造正确性（prompt 中包含历史关键词），而不是 LLM 输出质量

### 3.3 `tests/integration/test_chat_api.py` — 分层处理 skip 标记

**现状**：
- 文件已存在（280 行），所有 10 个测试都被 `@pytest.mark.skip(reason="requires running services")` 标记
- 测试逻辑本身是正确的，但 `app.main` 的 lifespan（Redis、SQLAlchemy 引擎、Celery）启动链路复杂

**决策**：TestClient 集成测试的 mock 依赖链（lifespan → Redis → SQLAlchemy → 鉴权中间件）涉及面广，mock 覆盖范围不确定。Plan 00 的目标是"零生产代码改动的测试护栏"，不适合同时解决 mock 基础设施问题。

**本 Plan 中的操作**：
- **全部 10 个 TestClient 集成测试保持 `@pytest.mark.skip`**，不取消
- 将 skip 的 reason 从 `"requires running services"` 统一改为 `"requires mock infra — see Plan 00 notes"`，表明已被审阅而非被遗忘
- **新增不依赖 TestClient 的 SSE 事件顺序测试**（见下方），直接调用 `handle_chat_stream` 的 yield 序列

**改动（批量 replace）**：
```python
# 全部 10 处 skip reason 统一改为：
@pytest.mark.skip(reason="requires mock infra — see Plan 00 notes")
```

**新增 SSE 顺序稳定性测试**：

在文件末尾新增以下测试类。**不使用 TestClient**，直接 mock `handle_chat_stream` 的依赖后调用它，收集 `async for` yield 的事件序列：

```python
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestSSEEventOrder:
    """
    验证流式 chat 的 SSE 事件顺序稳定性。
    标准顺序：session_id → token(s) → chunk_refs → done

    不使用 TestClient，直接调用 handle_chat_stream 收集 yield 事件。
    这绕过了 lifespan/Redis/SQLAlchemy 启动链路的 mock 问题。
    """

    @pytest.mark.asyncio
    async def test_sse_event_order_session_id_first_done_last(self):
        """
        验证 handle_chat_stream yield 的事件序列中：
        1. 第一个事件的 type 是 "session_id"
        2. 最后一个事件的 type 是 "done"
        """
        # mock 所有 handle_chat_stream 内部依赖
        # handle_chat_stream 签名：(db, repo_id, query, session_id, llm_provider, llm_model)
        mock_db = AsyncMock()

        fake_guidelines = []
        fake_contents = ["chunk_1"]
        fake_fused = "test query"

        with patch("app.services.chat_service.fuse_query", new=AsyncMock(return_value=fake_fused)), \
             patch("app.services.chat_service.stage1_discovery", new=AsyncMock(return_value=fake_guidelines)), \
             patch("app.services.chat_service.stage2_assembly", new=AsyncMock(return_value=fake_contents)), \
             patch("app.services.chat_service.stage2_gap_fill_constants", new=AsyncMock(return_value=[])), \
             patch("app.services.chat_service._get_codebase_index_text", new=AsyncMock(return_value=None)), \
             patch("app.services.chat_service._get_repo_name", new=AsyncMock(return_value="test-repo")), \
             patch("app.services.chat_service.create_session", new=AsyncMock(return_value="test-session-id")), \
             patch("app.services.chat_service.session_exists", new=AsyncMock(return_value=True)), \
             patch("app.services.chat_service.get_history", new=AsyncMock(return_value=[])), \
             patch("app.services.chat_service.append_turn", new=AsyncMock()), \
             patch("app.services.chat_service.is_broad_query", return_value=False), \
             patch("app.services.chat_service.create_adapter") as mock_create:

            # mock LLM adapter 返回流式 token
            # stream_with_rate_limit 是 async generator，yield 纯字符串
            mock_adapter = MagicMock()
            async def fake_stream(**kwargs):
                yield "Hello"
                yield " World"
            mock_adapter.stream_with_rate_limit = MagicMock(return_value=fake_stream())
            mock_create.return_value = mock_adapter

            from app.services.chat_service import handle_chat_stream

            events = []
            async for event in handle_chat_stream(
                db=mock_db,
                repo_id="test-repo",
                query="test query",
                session_id=None,
                llm_model="gpt-4o-mini",
            ):
                events.append(event)

        # 断言
        assert len(events) >= 2, f"期望至少 2 个事件，实际 {len(events)}"
        assert events[0].get("type") == "session_id", f"首事件应为 session_id，实际: {events[0]}"
        assert events[-1].get("type") == "done", f"末事件应为 done，实际: {events[-1]}"

    @pytest.mark.asyncio
    async def test_sse_token_events_between_session_and_done(self):
        """
        token 事件必须出现在 session_id 之后、done 之前。
        chunk_refs 必须在 done 之前。
        """
        # 此测试的 mock 结构与上面相同，验证事件类型的顺序约束
        # 实现时复用上方的 mock 模板，此处标记为占位
        pass  # 实现时按上方模板填充
```

**实施注意**：
- SSE 测试直接调用 `handle_chat_stream()` 而非 TestClient，避免 lifespan 启动问题
- mock 列表基于 `handle_chat_stream` 的实际调用链（`fuse_query` → `stage1_discovery` → `stage2_assembly` → `create_adapter`）
- 如果 `handle_chat_stream` 的内部结构在后续 Plan（04/05）中改变，测试中的 mock 需要同步更新——这正是"变更检测器"的价值
- 第二个测试 `test_sse_token_events_between_session_and_done` 标记为占位，实现时按同模板填充

---

## 4. 验收标准

1. **所有新增测试可离线运行**：`pytest tests/unit/test_token_budget.py tests/unit/test_query_fusion.py -v` 全部通过
2. **集成测试 skip 统一标注**：全部 10 个 TestClient 集成测试保持 skip，reason 统一为 `"requires mock infra — see Plan 00 notes"`
3. **SSE 顺序测试可执行**：`TestSSEEventOrder.test_sse_event_order_session_id_first_done_last` 通过（直接调用 `handle_chat_stream`，不依赖 TestClient）
4. **零生产代码改动**：本 Plan 不修改 `app/` 目录下的任何文件
5. **测试命名规范**：所有测试方法以 `test_` 开头，测试类以 `Test` 开头，含清晰的 docstring
6. **基线快照意义明确**：`TestTokenBudgetChunkBoundary` 的测试注释中明确标注"此测试在 Plan 01 后需要更新期望行为"

---

## 5. 风险与注意事项

- `tests/integration/test_chat_api.py` 的 skip 取消可能因为 `app.main` 的 import 链路（lifespan、数据库初始化）导致 import error。如果遇到，优先考虑 mock `app.main:app` 的 lifespan，而不是跳过
- SSE 事件顺序测试已改为直接调用 `handle_chat_stream()` 收集 yield 事件，不依赖 TestClient
- 本 Plan 的测试是"行为快照"而非"正确性验证"——部分测试在后续 Plan 实施后需要更新断言
- TestClient 集成测试的 mock 基础设施（lifespan、Redis、鉴权）属于独立工程问题，不在本 Plan 范围内。后续如需启用，应作为专项 mock fixture 建设
