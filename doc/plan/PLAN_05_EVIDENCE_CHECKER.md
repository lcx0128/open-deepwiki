# Plan 05: Phase 3A — 证据充分性判断 + 自动二轮检索

> 优先级：中高（Phase 3）
> 前置依赖：Plan 03（Code Searcher）、Plan 04（三路搜索集成）
> 预计改动文件数：3
> 策略文档对应：功能三（证据充分性判断 + 自动二轮检索）

---

## 1. 目标

在普通 chat 的首轮检索之后，插入一个"证据充分性判断"步骤。当判断证据不足时，自动触发一次补充检索（grep + 路径搜索），然后再进入 LLM 生成。最多执行一次补充检索（不循环），控制时延。

**改造前行为**：普通 chat 单轮决策——检索一次 → 拼 prompt → 回答。无论首轮命中质量如何，都直接生成答案。

**改造后行为**：首轮检索后进行充分性判断，若不足则自动补一轮检索，基于合并后的证据回答。对用户完全透明。

---

## 2. 涉及文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `app/services/evidence_checker.py` | 新增 | 证据充分性判断模块 |
| `app/services/chat_service.py` | 修改 | 在三路检索后插入充分性判断 + 条件补检索 |
| `tests/unit/test_evidence_checker.py` | 新增 | 充分性判断规则覆盖测试 |

---

## 3. 详细实施方案

### 3.1 `app/services/evidence_checker.py` — 新增文件

```python
"""
evidence_checker.py — 证据充分性判断

对首轮检索结果进行轻量评估，判断当前证据是否足以回答用户问题。
若不足，返回缺失方面和补充检索建议。

设计原则：
- 第一阶段使用纯规则判断（零延迟）
- 仅对"规则无法确定"的中间情况考虑 LLM 辅助（当前不实现，预留接口）
- 最多触发一次补充检索，不循环
"""
import re
import logging
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class EvidenceCheckResult:
    """证据充分性判断结果"""
    is_sufficient: bool
    missing_aspects: List[str] = field(default_factory=list)
    suggested_queries: List[str] = field(default_factory=list)
```

#### 3.1.1 复杂问题特征词

```python
# 暗示需要跨文件证据的查询特征
_COMPLEX_PATTERNS = {
    "call_chain": [
        r'调用链', r'调用关系', r'执行流程', r'完整流程', r'执行路径',
        r'从.*到', r'全链路', r'数据流', r'请求链路',
        r'call chain', r'call graph', r'execution flow', r'full flow',
        r'end.to.end', r'data flow', r'request path',
    ],
    "implementation": [
        r'怎么实现', r'如何实现', r'实现原理', r'实现逻辑', r'实现细节',
        r'底层.*实现', r'核心.*实现', r'源码.*分析',
        r'how.*implement', r'implementation', r'how.*work',
    ],
    "why": [
        r'为什么', r'为何', r'原因是', r'什么原因',
        r'why', r'reason', r'root cause',
    ],
    "cross_module": [
        r'跨.*模块', r'多个.*文件', r'哪些.*模块', r'关联.*文件',
        r'所有.*相关', r'涉及.*哪些',
        r'across.*module', r'which.*files', r'related.*modules',
    ],
}
```

#### 3.1.2 核心判断函数

```python
def check_evidence_sufficiency(
    query: str,
    code_contents: List[str],
    guidelines: list,  # List[CodeGuideline]
) -> EvidenceCheckResult:
    """
    判断当前检索结果是否足以回答用户问题。

    第一阶段：规则判断（无 LLM，零延迟）

    规则：
    1. code_contents 为空 → 不充分
    2. guidelines < 3 且 query 含复杂特征词 → 不充分
    3. 命中文件全在同一个文件内 且 query 暗示跨模块 → 不充分
    4. guidelines >= 5 且无复杂特征 → 充分
    5. 其余情况 → 充分（保守策略，避免过度补检索）

    参数:
        query: 用户原始查询
        code_contents: stage2_assembly 返回的完整代码片段列表
        guidelines: 合并后的 CodeGuideline 列表

    返回:
        EvidenceCheckResult
    """
    # 规则 1：完全无结果
    if not code_contents:
        suggested = _extract_identifiers_for_grep(query)
        return EvidenceCheckResult(
            is_sufficient=False,
            missing_aspects=["无检索结果"],
            suggested_queries=suggested,
        )

    # 分析查询复杂度
    detected_aspects = _detect_complex_aspects(query)
    query_is_complex = len(detected_aspects) > 0

    # 分析命中文件多样性
    hit_files = set()
    for g in guidelines:
        fp = getattr(g, 'file_path', '')
        if fp:
            hit_files.add(fp)

    # 规则 2：结果稀少 + 查询复杂
    if len(guidelines) < 3 and query_is_complex:
        missing = detected_aspects
        suggested = _extract_identifiers_for_grep(query)
        return EvidenceCheckResult(
            is_sufficient=False,
            missing_aspects=missing,
            suggested_queries=suggested,
        )

    # 规则 3：单文件命中 + 查询暗示跨模块
    is_cross_module = "cross_module" in detected_aspects or "call_chain" in detected_aspects
    if len(hit_files) <= 1 and is_cross_module:
        missing = detected_aspects
        suggested = _extract_identifiers_for_grep(query)
        # 从已有 guidelines 中提取 symbol 名作为补充搜索关键词
        for g in guidelines[:5]:
            name = getattr(g, 'name', '')
            if name and name not in suggested:
                suggested.append(name)
        return EvidenceCheckResult(
            is_sufficient=False,
            missing_aspects=missing,
            suggested_queries=suggested[:10],
        )

    # 规则 4 & 5：其余情况视为充分
    return EvidenceCheckResult(is_sufficient=True)


def _detect_complex_aspects(query: str) -> List[str]:
    """检测查询中的复杂问题特征，返回匹配的 aspect 列表"""
    detected = []
    for aspect, patterns in _COMPLEX_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, query, re.IGNORECASE):
                detected.append(aspect)
                break
    return detected


def _extract_identifiers_for_grep(query: str) -> List[str]:
    """
    从查询中提取可用于 grep 补充搜索的标识符。
    与 code_searcher.extract_grep_patterns 功能类似但更保守：
    只提取高置信度的标识符。
    """
    identifiers = []

    # CamelCase
    camel = re.findall(r'\b([A-Z][a-z]+(?:[A-Z][a-z0-9]+)+)\b', query)
    identifiers.extend(camel)

    # snake_case（至少一个下划线）
    snake = re.findall(r'\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b', query)
    identifiers.extend(snake)

    # ALL_CAPS
    caps = re.findall(r'\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\b', query)
    identifiers.extend(caps)

    # 引号内字面量
    quoted = re.findall(r'["\']([^"\']{3,})["\']', query)
    identifiers.extend(quoted)

    # 去重
    seen = set()
    unique = []
    for ident in identifiers:
        if ident not in seen:
            seen.add(ident)
            unique.append(ident)

    return unique[:10]
```

### 3.2 `app/services/chat_service.py` — 插入充分性判断 + 补检索

#### 3.2.1 新增 import

```python
from app.services.evidence_checker import check_evidence_sufficiency
from app.services.code_searcher import grep_codebase, search_file_paths, extract_grep_patterns
```

#### 3.2.2 新增补检索私有函数

在 `_three_way_retrieval()` 之后新增：

```python
async def _run_supplemental_retrieval(
    missing_aspects: List[str],
    suggested_queries: List[str],
    repo_id: str,
    existing_guidelines: list,
    existing_contents: List[str],
    existing_weights: List[float],
    index_data: Optional[dict] = None,
    repo_dir: Optional[str] = None,
) -> tuple:
    """
    补充检索：根据证据充分性判断的建议，执行 grep + 路径搜索。

    返回:
        (updated_guidelines, updated_contents, updated_weights)
    """
    if not suggested_queries:
        return existing_guidelines, existing_contents, existing_weights

    from app.services.two_stage_retriever import merge_retrieval_results, stage2_assembly

    # 并行执行 grep 和路径搜索
    async def _noop():
        return []

    grep_task = grep_codebase(repo_id, suggested_queries[:5], repo_dir=repo_dir)
    path_query = " ".join(suggested_queries[:5])
    path_task = search_file_paths(repo_id, path_query, index_data) if index_data else _noop()

    grep_matches, path_files = await asyncio.gather(
        grep_task,
        path_task,
        return_exceptions=True,
    )
    if isinstance(grep_matches, Exception):
        grep_matches = []
    if isinstance(path_files, Exception):
        path_files = []

    if not grep_matches and not path_files:
        return existing_guidelines, existing_contents, existing_weights

    # 用 merge 去重并获取新 chunk（路径 + grep 一起合并）
    supplemental_guidelines = await merge_retrieval_results(
        existing_guidelines, path_files, grep_matches, repo_id
    )

    # 找出新增的 chunk_id
    existing_ids = {g.chunk_id for g in existing_guidelines}
    new_guidelines = [g for g in supplemental_guidelines if g.chunk_id not in existing_ids]
    new_chunk_ids = [g.chunk_id for g in new_guidelines][:5]  # 最多补 5 个 chunk

    if new_chunk_ids:
        new_contents = await stage2_assembly(new_chunk_ids, repo_id)
        # 权重同步（Plan 01 不变量）
        new_weights = [g.relevance_score for g in new_guidelines[:len(new_contents)]]
        return (
            supplemental_guidelines,
            existing_contents + new_contents,
            existing_weights + new_weights,
        )

    return supplemental_guidelines, existing_contents, existing_weights
```

#### 3.2.3 修改 `handle_chat()` — 在 gap_fill 之后、join 之前插入

在 `handle_chat()` 的 gap_fill 代码块之后（约第 193 行之后），`rag_context = "\n\n---\n\n".join(code_contents)` 之前，插入：

```python
    # 证据充分性判断 + 条件补检索（超时 8 秒静默降级）
    evidence_result = check_evidence_sufficiency(fused_query, code_contents, guidelines)
    if not evidence_result.is_sufficient:
        logger.info(
            f"[ChatService] 证据不足，触发补检索: "
            f"missing={evidence_result.missing_aspects}, "
            f"suggested={evidence_result.suggested_queries[:3]}"
        )
        try:
            guidelines, code_contents, chunk_weights = await asyncio.wait_for(
                _run_supplemental_retrieval(
                    evidence_result.missing_aspects,
                    evidence_result.suggested_queries,
                    repo_id,
                    guidelines,
                    code_contents,
                    chunk_weights,
                    index_data=index_data,
                    repo_dir=repo_dir,
                ),
                timeout=8.0,
            )
        except asyncio.TimeoutError:
            logger.warning("[ChatService] 补检索超时 (8s)，跳过")
```

#### 3.2.4 修改 `handle_chat_stream()` — 同样插入

在 `handle_chat_stream()` 的 gap_fill 之后、join 之前，插入相同的充分性判断代码。

**额外**：在流式版本中，补检索阶段可向前端发送进度事件：

```python
    evidence_result = check_evidence_sufficiency(fused_query, code_contents, guidelines)
    if not evidence_result.is_sufficient:
        yield {"type": "progress", "message": "正在补充检索相关代码..."}
        guidelines, code_contents = await _run_supplemental_retrieval(
            evidence_result.missing_aspects,
            evidence_result.suggested_queries,
            repo_id,
            guidelines,
            code_contents,
        )
```

**注意**：前端需要能处理 `{"type": "progress"}` 事件类型。如果前端尚未支持，暂时不发送此事件（仅日志），待前端适配后启用。

#### 3.2.5 修改 `handle_deep_research_stream()` — 同样插入

Deep Research 的每一轮迭代都重新检索，因此充分性判断在每轮的 gap_fill 之后插入。但 Deep Research 本身已有多轮机制，补检索的价值相对较小。可选择只在 `is_first` 时触发补检索：

```python
    if is_first:
        evidence_result = check_evidence_sufficiency(fused_query, code_contents, guidelines)
        if not evidence_result.is_sufficient:
            guidelines, code_contents = await _run_supplemental_retrieval(
                evidence_result.missing_aspects,
                evidence_result.suggested_queries,
                repo_id,
                guidelines,
                code_contents,
            )
```

---

## 4. 测试要求

### 4.1 `tests/unit/test_evidence_checker.py`（新建）

```python
"""
Unit tests for app/services/evidence_checker.py
验证证据充分性判断规则的正确性。
"""
import pytest
from app.services.evidence_checker import (
    check_evidence_sufficiency,
    EvidenceCheckResult,
    _detect_complex_aspects,
    _extract_identifiers_for_grep,
)


class TestCheckEvidenceSufficiency:
    def test_empty_contents_returns_insufficient(self):
        """code_contents 为空时应返回 is_sufficient=False。"""
        result = check_evidence_sufficiency("any query", [], [])
        assert result.is_sufficient is False
        assert "无检索结果" in result.missing_aspects

    def test_few_guidelines_complex_query_returns_insufficient(self):
        """结果稀少（< 3）+ 查询复杂 → 不充分。"""
        # 构造少于 3 个 guideline 的 mock 对象
        class FakeGuideline:
            def __init__(self, fp):
                self.file_path = fp
                self.name = "some_func"
                self.chunk_id = fp

        guidelines = [FakeGuideline("a.py"), FakeGuideline("b.py")]
        contents = ["chunk_a", "chunk_b"]
        result = check_evidence_sufficiency(
            "handle_chat_stream 的完整调用链是什么", contents, guidelines
        )
        assert result.is_sufficient is False
        assert "call_chain" in result.missing_aspects

    def test_single_file_cross_module_query_returns_insufficient(self):
        """单文件命中 + 跨模块查询 → 不充分。"""
        class FakeGuideline:
            def __init__(self):
                self.file_path = "app/main.py"
                self.name = "app"
                self.chunk_id = "id1"

        guidelines = [FakeGuideline() for _ in range(5)]
        contents = ["chunk"] * 5
        result = check_evidence_sufficiency(
            "这个功能涉及哪些模块", contents, guidelines
        )
        assert result.is_sufficient is False

    def test_sufficient_results_returns_sufficient(self):
        """充足的结果 + 简单查询 → 充分。"""
        class FakeGuideline:
            def __init__(self, fp, name):
                self.file_path = fp
                self.name = name
                self.chunk_id = fp

        guidelines = [
            FakeGuideline("a.py", "func_a"),
            FakeGuideline("b.py", "func_b"),
            FakeGuideline("c.py", "func_c"),
            FakeGuideline("d.py", "func_d"),
            FakeGuideline("e.py", "func_e"),
        ]
        contents = ["chunk"] * 5
        result = check_evidence_sufficiency(
            "BUDGET_RATIO 这个常量是多少", contents, guidelines
        )
        assert result.is_sufficient is True

    def test_suggested_queries_contain_identifiers(self):
        """建议查询应包含从 query 中提取的标识符。"""
        result = check_evidence_sufficiency(
            "handle_chat_stream 完整流程", [], []
        )
        assert "handle_chat_stream" in result.suggested_queries


class TestDetectComplexAspects:
    def test_call_chain_detected(self):
        aspects = _detect_complex_aspects("完整调用链是什么")
        assert "call_chain" in aspects

    def test_implementation_detected(self):
        aspects = _detect_complex_aspects("这个功能怎么实现的")
        assert "implementation" in aspects

    def test_why_detected(self):
        aspects = _detect_complex_aspects("为什么这里会失败")
        assert "why" in aspects

    def test_simple_query_no_aspects(self):
        aspects = _detect_complex_aspects("BUDGET_RATIO 的值是多少")
        assert len(aspects) == 0


class TestExtractIdentifiers:
    def test_camel_case(self):
        ids = _extract_identifiers_for_grep("ChatService 在哪里")
        assert "ChatService" in ids

    def test_snake_case(self):
        ids = _extract_identifiers_for_grep("handle_chat_stream 函数")
        assert "handle_chat_stream" in ids

    def test_quoted_literal(self):
        ids = _extract_identifiers_for_grep('找 "context_length" 在哪')
        assert "context_length" in ids

    def test_max_10_results(self):
        long_query = " ".join([f"func_{i}_name" for i in range(20)])
        ids = _extract_identifiers_for_grep(long_query)
        assert len(ids) <= 10
```

---

## 5. 验收标准

1. **空结果触发补检索**：首轮 `code_contents` 为空时，自动触发补充 grep
2. **复杂查询触发补检索**：query 含"调用链"/"完整流程"/"为什么"等词且结果稀少时触发
3. **单文件+跨模块触发**：命中全在同一文件但 query 暗示跨模块时触发
4. **简单查询不触发**：足够多结果 + 简单查询时不触发补检索
5. **最多一次补检索**：不循环补检索，控制时延
6. **流式版本可选进度**：`handle_chat_stream` 在补检索时可发送 `progress` 事件
7. **所有测试通过**：`pytest tests/unit/test_evidence_checker.py -v`

---

## 6. 风险与注意事项

- **补检索延迟 — 硬上限约束**：补检索增加一次 grep + merge + stage2_assembly 的时延。硬上限如下：
  - `suggested_queries` 传入 `grep_codebase` 时最多 **5 个 pattern**（`suggested_queries[:5]`）
  - `_run_supplemental_retrieval` 中 `merge_retrieval_results` 的 grep_matches 受 Plan 04 硬上限约束（最多 20 条）
  - 补取新 chunk 上限：**最多 5 个**（`new_chunk_ids[:5]`，已在代码中体现）
  - 整体补检索超时：使用 `asyncio.wait_for(_run_supplemental_retrieval(...), timeout=8.0)` 包裹调用，超时时静默降级为不补检索
- **假阳性过多**：如果规则过于激进（如"为什么"几乎总是触发），会导致大量不必要的补检索。初始部署后需根据日志调优规则阈值
- **前端 `progress` 事件兼容**：如果前端未处理 `{"type": "progress"}`，发送该事件可能被忽略（不影响功能）或导致前端 warning。建议先不发送，待前端适配后启用
- **`_extract_identifiers_for_grep` 与 `extract_grep_patterns` 功能重叠**：两者逻辑类似但 evidence_checker 版本更保守。考虑合并为同一函数，通过参数控制激进程度
- **SSE `progress` 事件前端兼容性**：引入 `{"type": "progress"}` 事件前，需确保前端对未知事件类型不崩溃。建议先在前端 `useEventSource` 中添加对未知类型的忽略处理，再启用此事件。初版可仅在日志中记录补检索行为，不向前端发送 `progress` 事件
