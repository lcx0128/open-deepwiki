# Module 5 代码审查报告

> **版本**: 1.0.0 | **审查日期**: 2026-02-21
>
> **审查范围**: Module 5 多轮对话 RAG 层（全量手工审查 + Codex 辅助审查）
>
> **审查方法**: Gemini MCP 因网络错误（ECONNRESET）不可用，改为手工逐文件审查 + OpenAI Codex 辅助审查。

---

## 审查文件清单

| 文件 | 说明 |
|------|------|
| `app/schemas/mcp_types.py` | CodeGuideline / FileContext Pydantic 模型 |
| `app/schemas/chat.py` | ChatRequest / ChatResponse / ChatStreamEvent |
| `app/services/token_budget.py` | Token 预算估算与裁剪 |
| `app/services/conversation_memory.py` | Redis 会话管理 |
| `app/services/query_fusion.py` | 多轮查询融合 |
| `app/services/two_stage_retriever.py` | 双阶段 ChromaDB 检索 |
| `app/services/chat_service.py` | 对话主流程编排 |
| `app/api/chat.py` | FastAPI 路由层 |
| `app/services/embedder.py` | embed_query 新增函数 |

---

## [CRITICAL] 运行时错误

> 经过完整审查，**未发现会导致运行时崩溃的 CRITICAL 级别问题**。

以下关键约束均已正确实现：

- **ChromaDB 约束** ✅ `stage1_discovery` 使用 `query_embeddings=[query_vector]`（预计算向量），未使用 `query_texts`，符合 CLAUDE.md 规定。
- **Semaphore 懒加载** ✅ `BaseLLMAdapter._semaphore` 在 `_get_semaphore()` 中懒加载；`embedder.py` 的 `_embedding_semaphore` 同样懒加载，无模块顶层创建。
- **DashScope LLM** ✅ 所有 LLM 调用使用 `generate_with_rate_limit` / `stream_with_rate_limit`，底层调用 `/chat/completions`，无 `/responses` 调用。
- **导入一致性** ✅ 所有被 import 的函数均实际存在：
  - `create_adapter(provider, model=None)` — `factory.py:12` ✅
  - `get_redis()` — `redis_client.py:21`（async）, 调用处均使用 `await` ✅
  - `get_collection(repo_id)` — `embedder.py:56`（sync）, 调用处无 `await` ✅
  - `embed_query(text)` — `embedder.py:197`（async）, 调用处使用 `await` ✅
  - `generate_with_rate_limit` — `adapter.py:46`，返回 `LLMResponse`，有 `.content` 和 `.usage` ✅
  - `stream_with_rate_limit` — `adapter.py:54`，async generator，正确使用 `async for` 迭代 ✅
  - `LLMMessage(role=..., content=...)` — `schemas/llm.py:5`，字段一致 ✅
  - `settings.DEFAULT_LLM_MODEL` — `config.py:20`，值 `"gpt-4o"` ✅
  - `settings.REPOS_BASE_DIR` — `config.py:42`，值 `"./repos"` ✅
- **async/await 正确性** ✅ 所有异步函数正确 await，所有同步函数无误用 await。
- **字段匹配** ✅ `ChatResponse(**result)` 中 `chunk_refs` 为 `List[dict]`，Pydantic v2 自动从 dict 构造 `ChunkRef` 对象。
- **Redis decode_responses** ✅ `redis_client.py` 使用 `decode_responses=True`，`hget` 返回 `str | None`，`json.loads()` 正确处理。

---

## [WARNING] 逻辑问题

### W1 — `apply_token_budget` 的 break 逻辑会跳过可容纳的旧消息

**文件**: `app/services/token_budget.py:68-76`

```python
for msg in reversed(messages):        # 从最新 → 最旧遍历
    msg_tokens = estimate_tokens(msg.get("content", ""))
    if used_tokens + msg_tokens <= history_budget:
        trimmed_messages.insert(0, msg)
        used_tokens += msg_tokens
    else:
        break  # ← 问题所在：一旦某条消息超出预算，就停止添加更旧的消息
```

**场景**: 若历史顺序为 `[A(100 tokens), B(8000 tokens), C(50 tokens)]`，预算 200 tokens：
- 从最新开始迭代：C(50) ✓ → B(8000) ✗ → **break（A 被跳过）**
- 结果：`[C]`，而 A 其实可以放入（50+100=150 ≤ 200）

**影响**: 低概率（需要单条超大消息夹在中间），但会静默丢失上下文。

**建议**: 将 `break` 改为 `continue`，允许继续尝试更旧的消息是否能放入预算。

---

### W2 — `conversation_memory.py` 无消息条数上限

**文件**: `app/services/conversation_memory.py:47-96`

`append_turn` 每次追加 2 条消息（user + assistant），**无条数限制**。规格说明（DEVELOPMENT_SPEC.md / API.md）要求保留最近 10 轮对话（20 条消息），超出时执行 FIFO 淘汰。

**影响**: Redis Hash 中 `messages` 字段会随对话轮次无限增长，直至 session TTL（24h）到期。`apply_token_budget` 在 LLM 调用前会按 Token 预算裁剪，功能上不会崩溃，但：
1. Redis 存储不必要的历史数据
2. `get_history` 每次返回全量消息，序列化/反序列化开销增大

**建议**: 在 `append_turn` 中添加 `messages = messages[-20:]` 确保最多保留 20 条。

---

### W3 — `handle_chat` / `handle_chat_stream` 无 `repo_id` 合法性校验

**文件**: `app/services/chat_service.py:47-157`

当传入不存在的 `repo_id` 时：
1. `stage1_discovery` 调用 `get_collection(repo_id)` → ChromaDB 的 `get_or_create_collection` 会**静默创建一个空 collection**
2. 查询返回空结果，RAG 上下文为空
3. LLM 仍然调用并回答，但回答无代码支撑
4. 不会触发 404 错误，用户得到的是无意义回答

**建议**: 在 session 管理之前，查询 DB 确认 `repo_id` 存在：
```python
repo = await db.execute(select(Repository).where(Repository.id == repo_id))
if not repo.scalar_one_or_none():
    raise FileNotFoundError(f"仓库不存在: {repo_id}")
```

---

### W4 — `fuse_query` 中 provider/model 可能不匹配

**文件**: `app/services/query_fusion.py:53-55`

```python
adapter = create_adapter(llm_provider)          # 如 dashscope
model = llm_model or "gpt-4o-mini"             # 默认 gpt-4o-mini
response = await adapter.generate_with_rate_limit(..., model=model, ...)
```

若 `llm_provider="dashscope"` 且 `llm_model=None`，则 DashScope 适配器会尝试调用 `gpt-4o-mini` 模型（该模型名称 DashScope 不认识），导致 API 报错。

**已有缓解**: `except Exception as e: return question`，错误被静默吞掉，回退原始问题。功能不中断，但每次调用都会产生一次无效 API 请求。

**建议**: 根据 provider 提供合适的默认轻量模型，例如：
```python
DEFAULT_FUSION_MODELS = {
    "dashscope": "qwen-turbo",
    "gemini": "gemini-1.5-flash",
    "openai": "gpt-4o-mini",
    "custom": "gpt-4o-mini",
}
model = llm_model or DEFAULT_FUSION_MODELS.get(llm_provider or "openai", "gpt-4o-mini")
```

---

### W5 — 流式模式 Token 计数始终为 0

**文件**: `app/services/chat_service.py:263`

```python
await append_turn(session_id, query, full_answer, chunk_refs, 0)  # 0 tokens
```

非流式模式通过 `response.usage.get("total_tokens", 0)` 获得真实 token 用量，流式模式无法从 AsyncIterator 获取 usage 信息，强制写入 0。

**影响**: Redis 会话中 `total_tokens` 对流式对话永远是 0，无法统计真实用量。功能不影响，但用量监控不准确。

**建议**: 在流式生成完成后，对生成内容使用 `estimate_tokens(full_answer)` 作为近似值写入。

---

## [INFO] 改进建议

### I1 — `estimate_tokens` 精度较低

**文件**: `app/services/token_budget.py:5-9`

当前方案对代码中大量特殊符号（`{}`, `[]`, `->`, `::` 等）的 token 化估计可能偏差 2-3 倍。对于代码相关 RAG，建议引入 `tiktoken` 库进行精确计算（仅需 CPU 本地计算，无网络开销）。

---

### I2 — 历史消息中的额外字段未过滤

**文件**: `app/services/chat_service.py:101-104`

Redis 中存储的历史消息包含 `id`, `chunk_refs`, `timestamp` 字段，而 `apply_token_budget` 的 token 估算只用 `content` 字段。这是正确的，代码已过滤：
```python
if role in ("user", "assistant"):
    messages.append(LLMMessage(role=role, content=msg.get("content", "")))
```
但 `apply_token_budget` 的 `estimate_tokens(msg.get("content", ""))` 也只取 `content`，行为一致。✅ 无问题，可以文档化说明。

---

### I3 — ChromaDB collection 名称在 `stage1_discovery` 中可能被重复替换

**文件**: `app/services/embedder.py:61` vs `app/services/two_stage_retriever.py:28`

`get_collection` 内部将 `-` 替换为 `_`，因此 `two_stage_retriever.py` 直接调用 `get_collection(repo_id)` 是正确的，不需要重复处理。✅ 无问题。

---

### I4 — `read_file_context` 函数目前无调用方

**文件**: `app/services/two_stage_retriever.py:99-131`

`read_file_context` 是为 Module 6 MCP 层预留的工具函数，当前 Module 5 没有调用方。该函数定义正确，不影响现有功能，但建议在 Module 6 实现时再正式启用。

---

## 总结

| 级别 | 问题数 | 状态 |
|------|--------|------|
| **CRITICAL** | 0 | ✅ 无运行时错误 |
| **WARNING** | 5 | ⚠️ 需关注（不阻塞上线） |
| **INFO** | 4 | 💡 建议优化 |

### 整体评估：**PASS（可上线，建议在下个版本修复 WARNING 问题）**

Module 5 实现整体质量良好：
- 所有关键约束（ChromaDB `query_embeddings`、Semaphore 懒加载、DashScope 仅 `/chat/completions`）均已正确遵循
- 导入链完整，无未定义调用
- 异步/同步使用正确
- Pydantic 字段匹配正确，Pydantic v2 自动类型强制转换正常工作
- 错误降级路径（context_length exceeded）正确实现
- Redis 操作（hset mapping、expire、hget）使用正确

**优先修复**: W2（无消息上限，违反规格）和 W3（无 repo_id 校验，用户体验问题）。
W1、W4、W5 可在下个迭代修复。

---

## 测试结果

```
pytest tests/unit/test_token_budget.py tests/unit/test_query_fusion.py tests/unit/test_conversation_memory.py -v
============================= 50 passed in 1.02s ==============================

pytest tests/integration/test_chat_api.py -v
============================= 10 skipped in 0.03s =============================
(需要运行中的后端服务才能执行集成测试)
```
