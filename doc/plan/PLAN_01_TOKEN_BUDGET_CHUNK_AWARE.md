# Plan 01: Phase 1A — Token Budget Chunk-aware 裁剪

> 优先级：最高（Phase 1）
> 前置依赖：Plan 00（测试基线）
> 预计改动文件数：3
> 策略文档对应：功能一（Token Budget Chunk-aware 裁剪）

---

## 1. 目标

将 RAG 上下文的裁剪策略从"按字符比例截断"升级为"以 chunk 为最小裁剪单位，按证据权重整块丢弃"。修复当前实现中最危险的数据损坏问题：代码片段被从中间切断，导致 LLM 读到残缺证据。

**改造前行为**：`apply_token_budget()` 第 62-65 行按 `ratio = context_budget / context_tokens` 截断字符串，可能在函数体中间、JSON 中间、调用链关键位置切断。

**改造后行为**：裁剪以完整 chunk 为单位，按权重从低到高整块丢弃，保证所有进入上下文的代码片段都是完整的。

---

## 2. 涉及文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `app/services/token_budget.py` | 修改 | 新增 `trim_chunks_to_budget()`，修改 `apply_token_budget()` |
| `app/services/chat_service.py` | 修改 | 三个入口函数中在 join 前插入 chunk 裁剪调用 |
| `tests/unit/test_token_budget.py` | 修改 | 更新 Plan 00 的基线测试 + 新增 chunk-aware 裁剪测试 |

---

## 3. 详细实施方案

### 3.1 `app/services/token_budget.py` — 新增 `trim_chunks_to_budget()`

**在文件末尾（第 79 行之后）新增函数**：

```python
def trim_chunks_to_budget(
    chunks: List[str],
    budget_tokens: int,
    weights: Optional[List[float]] = None,
) -> List[str]:
    """
    以 chunk 为最小裁剪单位，按证据权重整块丢弃，直到总 token 数 <= budget_tokens。

    参数:
        chunks: 完整 chunk 正文列表（来自 stage2_assembly 输出）
        budget_tokens: 允许的最大 token 数
        weights: 可选，对应 CodeGuideline.relevance_score，无权重时视为均等

    返回:
        裁剪后的 chunk 列表，保持原始顺序（不按权重排列输出）

    策略:
        1. 计算每个 chunk 的 token 数
        2. 按权重降序确定保留集合（先保留权重最高的）
        3. **同权重时按原始索引升序**（稳定保序，确定性结果）
        4. 从最低权重的 chunk 开始丢弃，直到总量 <= budget
        5. 输出时按原始索引顺序重排（保证代码上下文的语义连贯性）
        6. 始终保留至少 1 条 chunk（防止 context 完全清空）

    确定性保证:
        - `sorted(key=lambda x: (-weight, original_index))` 天然稳定
        - 相同 weight 的 chunk 始终按输入顺序保留
        - `weights=None` 时所有 chunk 权重均为 0.5，保留顺序 = 原始顺序从前往后
    """
```

**具体实现逻辑**：

```python
def trim_chunks_to_budget(
    chunks: List[str],
    budget_tokens: int,
    weights: Optional[List[float]] = None,
) -> List[str]:
    if not chunks:
        return []

    # 1. 计算每个 chunk 的 token 数和索引
    chunk_info = []
    for i, chunk in enumerate(chunks):
        tokens = estimate_tokens(chunk)
        weight = weights[i] if weights and i < len(weights) else 0.5
        chunk_info.append((i, chunk, tokens, weight))

    total_tokens = sum(info[2] for info in chunk_info)

    # 2. 如果总量在预算内，直接返回全部
    if total_tokens <= budget_tokens:
        return list(chunks)

    # 3. 按权重降序排列，同权重按原始索引升序（稳定保序）
    #    保证：weights 相同时，先出现的 chunk 优先保留
    sorted_by_weight = sorted(chunk_info, key=lambda x: (-x[3], x[0]))

    # 4. 逐个添加直到达到预算上限，但至少保留第一个（最高权重）
    kept_indices = set()
    running_tokens = 0

    for i, chunk, tokens, weight in sorted_by_weight:
        if running_tokens + tokens <= budget_tokens or not kept_indices:
            kept_indices.add(i)
            running_tokens += tokens
        # 如果添加当前 chunk 会超预算且已经有保留项，跳过

    # 5. 按原始索引顺序重排输出
    result = [chunks[i] for i in sorted(kept_indices)]
    return result
```

**需要的 import 变更**：

当前第 1 行：`from typing import List, Tuple`

修改为：`from typing import List, Optional, Tuple`

### 3.2 `app/services/token_budget.py` — 修改 `apply_token_budget()`

**删除第 62-65 行的字符比例截断逻辑**：

```python
# 当前代码（第 60-65 行）——需要删除
    # 裁剪 RAG 上下文
    context_tokens = estimate_tokens(rag_context)
    if context_tokens > context_budget:
        # 按字符比例截断
        ratio = context_budget / context_tokens
        rag_context = rag_context[:int(len(rag_context) * ratio)]
```

**替换为**：

```python
    # 裁剪 RAG 上下文（保持完整性，不做字符截断）
    # chunk-aware 裁剪已在调用方完成（chat_service 中的 trim_chunks_to_budget）
    # 此处仅做最终安全检查：如果 context 仍超预算，按 chunk 分隔符整块丢弃
    context_tokens = estimate_tokens(rag_context)
    if context_tokens > context_budget:
        chunks = rag_context.split("\n\n---\n\n")
        trimmed = trim_chunks_to_budget(chunks, context_budget)
        rag_context = "\n\n---\n\n".join(trimmed)
```

**设计意图**：
- 主要的 chunk-aware 裁剪在 `chat_service.py` 中的 join 之前完成（带权重）
- `apply_token_budget` 中保留一道安全网：如果 context 仍超预算（如 planner 注入的目标文件导致超限），按分隔符分块后无权重裁剪
- 完全移除字符级截断，保证不会出现半截 chunk

### 3.3 `app/services/chat_service.py` — 三个入口函数插入 chunk 裁剪

需要修改的三个位置：

#### 3.3.1 `handle_chat()` — 约第 213 行

**当前代码**（第 213-216 行）：

```python
    rag_context = "\n\n---\n\n".join(code_contents)
    trimmed_history, trimmed_context = apply_token_budget(
        history, model, system_prompt, rag_context, query
    )
```

**修改为**：

```python
    # Chunk-aware 裁剪：在 join 前按权重整块丢弃
    context_budget = _compute_context_budget(model, system_prompt, query)
    code_contents = trim_chunks_to_budget(code_contents, context_budget, chunk_weights)

    rag_context = "\n\n---\n\n".join(code_contents)
    trimmed_history, trimmed_context = apply_token_budget(
        history, model, system_prompt, rag_context, query
    )
```

**⚠ 权重对齐——关键约束**：

`code_contents` 和 `chunk_weights` 必须始终保持位置同步。当前 `handle_chat()` 的插入顺序为：

1. `stage2_assembly` → `code_contents`（与 `guidelines[:10]` 位置对齐）
2. `gap_contents + code_contents` → gap-fill prepend（前插若干项）
3. `code_contents.insert(0, planner_content)` → planner prepend（前插若干项）

如果只用 `guidelines[:len(code_contents)]` 做权重映射，gap-fill 和 planner 前插的项会把位置全部打乱。**禁止使用位置对齐回填权重**。

**正确做法**：在 `code_contents` 的每次 mutation 处同步维护 `chunk_weights`：

```python
    # 4. Stage 2: 获取完整代码
    top_chunk_ids = [g.chunk_id for g in guidelines[:10]]
    code_contents = await stage2_assembly(top_chunk_ids, repo_id)
    chunk_weights = [g.relevance_score for g in guidelines[:len(code_contents)]]

    # 4.5. gap-fill：前插 → weights 同步前插
    try:
        gap_contents = await stage2_gap_fill_constants(guidelines[:10], repo_id)
        if gap_contents:
            code_contents = gap_contents + code_contents
            chunk_weights = [0.7] * len(gap_contents) + chunk_weights
    except Exception as _gf:
        logger.debug(f"[ChatService] gap-fill failed (non-fatal): {_gf}")

    # 5. planner：前插 → weights 同步前插
    if codebase_index_text and is_broad_query(fused_query):
        planned_targets = await plan_retrieval(...)
        for target in planned_targets[:3]:
            try:
                content = await read_targeted_context(repo_id, target)
                if content:
                    code_contents.insert(0, content)
                    chunk_weights.insert(0, 0.5)  # planner 默认权重
            except Exception:
                pass

    # 6. Chunk-aware 裁剪（weights 与 code_contents 已同步）
    context_budget = _compute_context_budget(model, system_prompt, query)
    code_contents = trim_chunks_to_budget(code_contents, context_budget, chunk_weights)
```

**规则**：后续 Plan（04/05/06）对 `code_contents` 的任何 append/insert/prepend 都必须在 `chunk_weights` 做对应操作。这是跨 Plan 的不变量。

- import 语句应移到文件顶部（第 12 行 `from app.services.token_budget import apply_token_budget, estimate_tokens` 已存在），只需补充 `trim_chunks_to_budget, MODEL_LIMITS, BUDGET_RATIO`

**更优方案**：将预算计算逻辑封装为辅助函数，避免在三个入口中重复：

在 `chat_service.py` 顶部新增私有函数：

```python
def _compute_context_budget(model: str, system_prompt: str, user_query: str) -> int:
    """计算 RAG 上下文可用的 token 预算"""
    limit = MODEL_LIMITS.get(model, 32000)
    budget = int(limit * BUDGET_RATIO)
    fixed_tokens = estimate_tokens(system_prompt) + estimate_tokens(user_query) + 100
    remaining = budget - fixed_tokens
    return int(max(remaining, 0) * CONTEXT_BUDGET_RATIO)
```

#### 3.3.2 `handle_chat_stream()` — 约第 347 行

**当前代码**（第 347-351 行）：

```python
    rag_context = "\n\n---\n\n".join(code_contents)

    trimmed_history, trimmed_context = apply_token_budget(
        history, model, system_prompt, rag_context, query
    )
```

**修改为**（同 3.3.1 的权重同步模式，`chunk_weights` 在上方的 stage2/gap-fill/planner 各步骤中已同步维护）：

```python
    # Chunk-aware 裁剪
    context_budget = _compute_context_budget(model, system_prompt, query)
    code_contents = trim_chunks_to_budget(code_contents, context_budget, chunk_weights)

    rag_context = "\n\n---\n\n".join(code_contents)

    trimmed_history, trimmed_context = apply_token_budget(
        history, model, system_prompt, rag_context, query
    )
```

#### 3.3.3 `handle_deep_research_stream()` — 约第 497 行

**当前代码**（第 497 行）：

```python
    rag_context = "\n\n---\n\n".join(code_contents)
```

之后在第 511-512 行：

```python
    trimmed_history, trimmed_context = apply_token_budget(
        history_msgs, model, system_prompt, rag_context, user_instruction
    )
```

**修改为**（在第 497 行前插入；`chunk_weights` 在上方各步骤中已同步维护）：

```python
    # Chunk-aware 裁剪
    context_budget = _compute_context_budget(model, system_prompt, user_instruction)
    code_contents = trim_chunks_to_budget(code_contents, context_budget, chunk_weights)

    rag_context = "\n\n---\n\n".join(code_contents)
```

### 3.4 `chat_service.py` 顶部 import 调整

**当前第 12 行**：

```python
from app.services.token_budget import apply_token_budget, estimate_tokens
```

**修改为**：

```python
from app.services.token_budget import (
    apply_token_budget, estimate_tokens, trim_chunks_to_budget,
    MODEL_LIMITS, BUDGET_RATIO, CONTEXT_BUDGET_RATIO,
)
```

---

## 4. 测试要求

### 4.1 更新 `tests/unit/test_token_budget.py`

**新增测试类** `TestTrimChunksToBudget`：

```python
class TestTrimChunksToBudget:
    def test_all_chunks_within_budget_returns_all(self):
        """总 token 在预算内时返回全部 chunk。"""
        chunks = ["short chunk 1", "short chunk 2", "short chunk 3"]
        result = trim_chunks_to_budget(chunks, 100000)
        assert result == chunks

    def test_low_weight_chunks_dropped_first(self):
        """超预算时，低权重 chunk 优先被丢弃，高权重保留。"""
        # 构造两个大小可控的 chunk（estimate_tokens 约为 len/4）
        chunk_a = "x" * 400   # ~100 tokens, weight=0.3
        chunk_b = "y" * 400   # ~100 tokens, weight=0.9
        chunks = [chunk_a, chunk_b]
        weights = [0.3, 0.9]
        # 预算 120：只够放一个 chunk → 应保留高权重的 chunk_b
        result = trim_chunks_to_budget(chunks, 120, weights)
        assert result == [chunk_b], f"应仅保留高权重 chunk, got {len(result)} chunks"

    def test_original_order_preserved(self):
        """返回的 chunk 保持原始顺序（不按权重排列）。"""
        # 三个小 chunk（各 ~2 tokens），预算够放两个但不够三个
        chunks = ["ab", "cd", "ef"]
        weights = [0.5, 0.9, 0.7]  # cd 权重最高，ef 次之
        # 预算 3 tokens：够两个（2+2=4? 不确定，用精确值）
        # estimate_tokens("ab") ≈ 1, 三个 ≈ 3, 预算 2 只够两个
        result = trim_chunks_to_budget(chunks, 2, weights)
        # 应保留 cd(0.9) 和 ef(0.7)，按原序输出：cd 在 ef 前
        assert len(result) == 2
        assert result == ["cd", "ef"], f"应按原序输出 ['cd', 'ef'], got {result}"

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
        """同权重时按原始顺序保留（前面的优先）。确定性保证。"""
        # 四个 ~25 token 的 chunk，总 ~100 tokens，预算只够 2 个
        chunks = ["a" * 100, "b" * 100, "c" * 100, "d" * 100]
        weights = [0.5, 0.5, 0.5, 0.5]
        result = trim_chunks_to_budget(chunks, 60, weights)
        # 同权重 → 原始索引升序 → 保留最前面的 chunk
        assert len(result) == 2
        assert result == [chunks[0], chunks[1]], (
            f"同权重应保留最靠前的 chunk, got indices "
            f"{[chunks.index(r) for r in result]}"
        )

    def test_no_weights_tiebreak_is_deterministic(self):
        """weights=None 时多次调用结果完全一致。"""
        chunks = ["a" * 100, "b" * 100, "c" * 100]
        r1 = trim_chunks_to_budget(chunks, 50)
        r2 = trim_chunks_to_budget(chunks, 50)
        assert r1 == r2

    def test_chunk_integrity_no_partial_content(self):
        """核心验证：裁剪后的每个 chunk 都必须与原始完全一致（不被截断）。"""
        chunks = [
            "def function_a():\n    return 'hello'\n",
            "class MyClass:\n    pass\n",
            "CONSTANT = 'value'\n",
        ]
        weights = [0.9, 0.5, 0.3]
        result = trim_chunks_to_budget(chunks, 20, weights)
        for r in result:
            assert r in chunks  # 每个输出 chunk 必须是某个原始 chunk 的完整副本
```

### 4.2 更新 Plan 00 中的基线测试

`TestTokenBudgetChunkBoundary.test_current_truncation_cuts_mid_chunk` 需要更新为验证新行为：

```python
def test_chunk_aware_truncation_preserves_chunk_boundary(self):
    """
    重构后行为：RAG context 超预算时按 chunk 整块丢弃。
    验证截断后的 context 中每个 chunk 都是完整的。
    """
    chunk_a = "// File: a.py\n" + "a" * 2000
    chunk_b = "// File: b.py\n" + "b" * 2000
    chunk_c = "// File: c.py\n" + "c" * 2000
    rag_context = "\n\n---\n\n".join([chunk_a, chunk_b, chunk_c])

    _, trimmed_ctx = apply_token_budget(
        [], "gpt-3.5-turbo",
        "system prompt " * 100,
        rag_context,
        "user query"
    )

    if len(trimmed_ctx) < len(rag_context):
        # 验证每个剩余 chunk 都是完整的原始 chunk
        remaining_chunks = trimmed_ctx.split("\n\n---\n\n")
        original_chunks = [chunk_a, chunk_b, chunk_c]
        for rc in remaining_chunks:
            assert rc in original_chunks, "裁剪后存在不完整的 chunk"
```

---

## 5. 验收标准

1. **字符截断完全移除**：`token_budget.py` 中不再存在 `rag_context[:int(len(rag_context) * ratio)]` 模式
2. **所有 chunk 完整性**：`TestTrimChunksToBudget.test_chunk_integrity_no_partial_content` 通过
3. **原始顺序保持**：`TestTrimChunksToBudget.test_original_order_preserved` 通过
4. **至少保留一个**：`TestTrimChunksToBudget.test_at_least_one_chunk_always_kept` 通过
5. **三个入口统一改造**：`handle_chat`、`handle_chat_stream`、`handle_deep_research_stream` 都在 join 前调用 `trim_chunks_to_budget`
6. **Plan 00 测试仍通过**：旧测试不 break（`test_rag_context_truncated_when_too_long` 可能需要微调断言但逻辑不变）
7. **现有 `pytest tests/unit/test_token_budget.py -v` 全部通过**

---

## 6. 风险与注意事项

- **权重对齐问题（跨 Plan 不变量）**：`code_contents` 和 `chunk_weights` 必须始终保持位置同步。每次对 `code_contents` 的 append/insert/prepend 都必须有对应的 `chunk_weights` 操作。后续 Plan（04 三路合并、05 补检索、06 依赖图扩展）在向 `code_contents` 插入新项时必须遵守此约束。`trim_chunks_to_budget` 在 `weights` 短于 `chunks` 时使用默认值 0.5 作为兜底，但不应依赖此行为
- **`apply_token_budget` 的安全网**：保留 `apply_token_budget` 中的备用裁剪（基于分隔符），作为防御层，但不再做字符级截断
- **预算计算一致性**（必须执行）：在 `token_budget.py` 中定义 `CONTEXT_BUDGET_RATIO = 0.6`，在 `apply_token_budget`（第 57 行）和 `_compute_context_budget` 中引用同一常量。禁止在两处硬编码 `0.6`，否则未来维护时极易出现不一致
