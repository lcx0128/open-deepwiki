# Plan 06: Phase 3B — 依赖图接入检索编排

> 优先级：中（Phase 3，与 Plan 05 并行或顺序）
> 前置依赖：Plan 05（证据充分性判断）
> 预计改动文件数：3
> 策略文档对应：功能四（依赖图接入检索编排）

---

## 1. 目标

将现有的 AST 依赖图能力（`dependency_graph.py` 中已有 `calls` 字段提取和 `build_dependency_graph()`）正式接入问答检索链路。当首轮检索命中某个函数后，系统可沿依赖图自动扩展一跳（callee 或 caller），将调用链相关的代码片段补入上下文。

**⚠ 精度定位**：本 Plan 实现的是 **name-based 近似依赖扩展**，不是精确的文件级依赖追踪。原因：现有 ChromaDB 中 `calls` 字段（`ChunkNode.to_metadata()` 第 40 行）仅存储被调用函数的**名称列表**（逗号分隔字符串），不包含被调用函数的文件路径或 chunk ID。因此在扩展 callee 时，只能按 `name` 做全局查询，可能命中同名函数（如 `create()`、`get()`）。通过 `limit=3` 和总量上限 5 控制误匹配影响。

**改造前行为**：检索命中函数后不追踪调用关系。对"完整实现流程"/"数据从哪来"类问题，答案容易在解释调用链时中断。

**改造后行为**：当证据充分性判断的 `missing_aspects` 包含"调用链"相关特征时，自动从依赖图扩展一跳，将上下游函数的 chunk 补入上下文。Token 消耗由 Plan 01 的 chunk-aware 裁剪控制。

**精度限制（已知且接受）**：
- `get_callees()` 精度高：从 caller 的 `calls` 字段提取 callee 名称，caller 由 `(name, file_path)` 唯一定位
- `expand_via_dependency_graph()` 精度中：用 callee 名称做全局 `name` 查询，可能匹配到不同文件中的同名函数。`limit=3` 缓解
- `get_callers()` 精度低：扫描所有 chunk 的 `calls` 字段做字符串包含检查，假阳性率较高。大仓库（>5000 chunks）直接跳过
- 未来如需精确依赖追踪，需将 `calls` 字段从 `"func_a,func_b"` 升级为 `"file_a.py:func_a,file_b.py:func_b"` 格式，并重建索引。这不在本 Plan 范围内

---

## 2. 涉及文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `app/services/dependency_graph.py` | 修改 | 新增 `get_callees()`、`get_callers()` 查询接口 |
| `app/services/two_stage_retriever.py` | 修改 | 新增 `expand_via_dependency_graph()` |
| `app/services/chat_service.py` | 修改 | 在补检索流程中调用依赖图扩展 |

---

## 3. 详细实施方案

### 3.1 `app/services/dependency_graph.py` — 新增查询接口

**当前文件结构**：
- `build_dependency_graph(chunks)` — 第 5-67 行，构建完整邻接表
- `get_orm_models(chunks)` — 第 70-98 行
- `get_file_summary(chunks)` — 第 101-144 行

**关键发现**：当前 `build_dependency_graph()` 接收 `List[ChunkNode]`，即内存中的 chunk 对象。但在 chat 链路中，chunk 数据存储在 ChromaDB 中，不是内存对象。因此新增的查询接口需要直接查 ChromaDB，而不是依赖 `build_dependency_graph()` 的内存邻接表。

**在文件末尾（第 144 行之后）新增**：

```python
async def get_callees(
    repo_id: str,
    symbol_name: str,
    file_path: Optional[str] = None,
) -> List[str]:
    """
    查询指定 symbol 调用的函数名列表（下游依赖）。

    参数:
        repo_id: 仓库 ID
        symbol_name: 函数/方法名
        file_path: 可选，限定来源文件（用于消歧同名函数）

    返回:
        被调用函数名列表

    实现方式:
        从 ChromaDB 中查询该 symbol 的 chunk，解析其 metadata.calls 字段。
        calls 字段在 ChunkNode.to_metadata() 中以逗号分隔存储。

    重名消歧:
        当 file_path 为空时返回空列表（保守策略），避免跨文件误匹配。
    """
    if not file_path:
        return []

    try:
        from app.services.embedder import get_collection
        collection = get_collection(repo_id)

        results = collection.get(
            where={"name": symbol_name, "file_path": file_path},
            include=["metadatas"],
            limit=1,
        )

        if results["ids"] and results["metadatas"]:
            metadata = results["metadatas"][0]
            calls_str = metadata.get("calls", "")
            if calls_str:
                return [c.strip() for c in calls_str.split(",") if c.strip()]
    except Exception as exc:
        logger.debug(f"[DepGraph] get_callees failed for {symbol_name}@{file_path}: {exc}")

    return []


async def get_callers(
    repo_id: str,
    symbol_name: str,
    file_path: Optional[str] = None,
) -> List[str]:
    """
    查询调用指定 symbol 的函数名列表（上游调用方）。

    参数:
        repo_id: 仓库 ID
        symbol_name: 被调用的函数/方法名
        file_path: 可选，限定目标 symbol 的来源文件

    返回:
        调用方函数名列表

    实现方式:
        遍历 ChromaDB 中所有 chunk，检查其 calls 字段是否包含 symbol_name。
        注意：这是一个相对昂贵的操作（扫描所有 chunk），
        因此仅在证据充分性判断明确需要时才调用。

    重名消歧:
        file_path 用于验证目标 symbol 确实存在于指定文件中。
        若 file_path 为空则返回空列表。
    """
    if not file_path:
        return []

    try:
        from app.services.embedder import get_collection
        collection = get_collection(repo_id)

        # 验证目标 symbol 存在
        target_check = collection.get(
            where={"name": symbol_name, "file_path": file_path},
            include=["ids"],
            limit=1,
        )
        if not target_check["ids"]:
            return []

        # 性能护栏：chunk 数 > 5000 时跳过全量扫描
        chunk_count = collection.count()
        if chunk_count > 5000:
            logger.warning(
                f"[DepGraph] get_callers skipped: repo {repo_id} has {chunk_count} chunks (>5000)"
            )
            return []

        # 扫描所有 chunk 的 calls 字段
        # ChromaDB 不支持 "calls contains X" 查询，需要全量拉取再过滤
        # 优化：只拉取 metadatas，不拉 documents（节省内存）
        all_chunks = collection.get(
            include=["metadatas"],
        )

        callers = []
        if all_chunks["metadatas"]:
            for metadata in all_chunks["metadatas"]:
                calls_str = metadata.get("calls", "")
                if symbol_name in calls_str.split(","):
                    caller_name = metadata.get("name", "")
                    if caller_name and caller_name != symbol_name:
                        callers.append(caller_name)

        return list(set(callers))[:10]  # 去重，最多 10 个
    except Exception as exc:
        logger.debug(f"[DepGraph] get_callers failed for {symbol_name}@{file_path}: {exc}")

    return []
```

**需要在文件顶部新增 import**：

当前第 1-2 行：

```python
from typing import List, Dict, Set
from app.schemas.chunk_node import ChunkNode
```

修改为：

```python
import logging
from typing import Dict, List, Optional, Set

from app.schemas.chunk_node import ChunkNode

logger = logging.getLogger(__name__)
```

### 3.2 `app/services/two_stage_retriever.py` — 新增 `expand_via_dependency_graph()`

**在 `merge_retrieval_results()` 之后（文件末尾）新增**：

```python
async def expand_via_dependency_graph(
    guidelines: List[CodeGuideline],
    repo_id: str,
    direction: str = "callee",
    max_hops: int = 1,
) -> List[CodeGuideline]:
    """
    沿依赖图扩展一跳，将调用链相关的 chunk 补入检索结果。

    参数:
        guidelines: 当前已有的 CodeGuideline 列表
        repo_id: 仓库 ID
        direction: 扩展方向，"callee"（下游被调用方）或 "caller"（上游调用方）
        max_hops: 最大扩展跳数（当前固定为 1）

    返回:
        补充的 CodeGuideline 列表（不含原始 guidelines 中已有的）

    策略:
        1. 从 guidelines 中提取 symbol_name + file_path（两者均非空才处理）
        2. 调用 get_callees() 或 get_callers() 得到扩展 symbol 列表
        3. 对每个扩展 symbol 在 ChromaDB 中做精确 name 查询，补取对应 chunk
        4. 去重后返回，每跳最多取 5 个 symbol（控制 token 膨胀）
    """
    from app.services.dependency_graph import get_callees, get_callers

    existing_ids = {g.chunk_id for g in guidelines}
    expanded = []
    processed_symbols = set()

    for g in guidelines:
        symbol_name = getattr(g, 'name', '')
        file_path = getattr(g, 'file_path', '')

        if not symbol_name or not file_path:
            continue
        if symbol_name in processed_symbols:
            continue
        processed_symbols.add(symbol_name)

        # 获取扩展 symbol 列表
        if direction == "callee":
            related_symbols = await get_callees(repo_id, symbol_name, file_path)
        elif direction == "caller":
            related_symbols = await get_callers(repo_id, symbol_name, file_path)
        else:
            continue

        # 最多处理 5 个扩展 symbol
        for rel_sym in related_symbols[:5]:
            if rel_sym in processed_symbols:
                continue
            processed_symbols.add(rel_sym)

            try:
                collection = get_collection(repo_id)
                sym_results = collection.get(
                    where={"name": rel_sym},
                    include=["metadatas", "documents", "ids"],
                    limit=3,  # 同名可能存在于多个文件
                )

                if sym_results["ids"]:
                    for i, chunk_id in enumerate(sym_results["ids"]):
                        if chunk_id in existing_ids:
                            continue
                        metadata = sym_results["metadatas"][i] if sym_results["metadatas"] else {}
                        doc = sym_results["documents"][i] if sym_results["documents"] else ""
                        first_line = doc.split("\n")[0][:100] if doc else ""

                        expanded.append(CodeGuideline(
                            chunk_id=chunk_id,
                            name=metadata.get("name", ""),
                            file_path=metadata.get("file_path", ""),
                            node_type=metadata.get("node_type", ""),
                            start_line=int(metadata.get("start_line", 0)),
                            end_line=int(metadata.get("end_line", 0)),
                            description=f"[dep-graph:{direction}] {first_line}",
                            relevance_score=0.65,  # 介于 path(0.6) 和 grep(0.75) 之间
                            source="dep_graph",
                        ))
                        existing_ids.add(chunk_id)
            except Exception as exc:
                logger.debug(f"[DepGraph] expand lookup failed for {rel_sym}: {exc}")

        # 控制总扩展量
        if len(expanded) >= 5:
            break

    logger.debug(
        f"[DepGraph] repo={repo_id}, direction={direction}, "
        f"expanded={len(expanded)} chunks from {len(processed_symbols)} symbols"
    )

    return expanded
```

### 3.3 `app/services/chat_service.py` — 在补检索中调用依赖图扩展

**修改 `_run_supplemental_retrieval()` 函数**（Plan 05 中新增的）：

在现有的 grep 补检索之后，添加依赖图扩展逻辑：

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
    fused_query: str = "",
) -> tuple:
    """
    补充检索：grep + 路径搜索 + 依赖图扩展。
    Plan 05 定义基础版（grep + 路径），Plan 06 在此基础上追加依赖图扩展。

    返回:
        (updated_guidelines, updated_contents, updated_weights)
    """
    from app.services.two_stage_retriever import (
        merge_retrieval_results, stage2_assembly, expand_via_dependency_graph,
    )

    updated_guidelines = list(existing_guidelines)
    updated_contents = list(existing_contents)
    updated_weights = list(existing_weights)

    # 1. Grep + 路径搜索补检索（Plan 05 范围）
    if suggested_queries:
        grep_task = grep_codebase(repo_id, suggested_queries[:5], repo_dir=repo_dir)
        path_query = " ".join(suggested_queries[:5])

        async def _noop():
            return []

        path_task = search_file_paths(repo_id, path_query, index_data) if index_data else _noop()

        grep_matches, path_files = await asyncio.gather(
            grep_task, path_task, return_exceptions=True,
        )
        if isinstance(grep_matches, Exception):
            grep_matches = []
        if isinstance(path_files, Exception):
            path_files = []

        if grep_matches or path_files:
            updated_guidelines = await merge_retrieval_results(
                updated_guidelines, path_files, grep_matches, repo_id
            )
            existing_ids = {g.chunk_id for g in existing_guidelines}
            new_guidelines = [g for g in updated_guidelines if g.chunk_id not in existing_ids]
            new_chunk_ids = [g.chunk_id for g in new_guidelines][:5]
            if new_chunk_ids:
                new_contents = await stage2_assembly(new_chunk_ids, repo_id)
                new_weights = [g.relevance_score for g in new_guidelines[:len(new_contents)]]
                updated_contents = updated_contents + new_contents
                updated_weights = updated_weights + new_weights

    # 2. 依赖图扩展（Plan 06 范围）
    #    仅当 missing_aspects 包含调用链相关特征时触发
    #    硬上限：5 秒超时
    call_chain_aspects = {"call_chain", "implementation"}
    if call_chain_aspects & set(missing_aspects):
        direction = _infer_expansion_direction(missing_aspects, query=fused_query)
        try:
            expanded = await asyncio.wait_for(
                expand_via_dependency_graph(
                    updated_guidelines, repo_id, direction=direction, max_hops=1
                ),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            logger.warning("[ChatService] 依赖图扩展超时 (5s)，跳过")
            expanded = []
        if expanded:
            updated_guidelines = updated_guidelines + expanded
            expanded_ids = [g.chunk_id for g in expanded]
            expanded_contents = await stage2_assembly(expanded_ids, repo_id)
            expanded_weights = [g.relevance_score for g in expanded[:len(expanded_contents)]]
            updated_contents = updated_contents + expanded_contents
            updated_weights = updated_weights + expanded_weights
            logger.info(
                f"[ChatService] 依赖图扩展: direction={direction}, "
                f"new_chunks={len(expanded)}"
            )

    return updated_guidelines, updated_contents, updated_weights
```

**新增方向推断辅助函数**：

```python
def _infer_expansion_direction(missing_aspects: List[str], query: str = "") -> str:
    """
    根据缺失方面和查询内容推断依赖图扩展方向。

    callee（追下游实现）：
      - "implementation" in missing_aspects
      - query 含 "怎么实现"/"流程"/"入口"/"底层"
      - 默认方向

    caller（追上游调用方）：
      - query 含 "谁调用"/"被哪里"/"触发"/"来源"/"哪里用到"
      - "call_chain" in missing_aspects 且不含 "implementation"
    """
    import re

    # 明确上游意图关键词
    _CALLER_PATTERNS = [
        r'谁调用', r'被.*调用', r'哪里.*触发', r'来源',
        r'哪里用到', r'被.*引用', r'上游',
        r'who.*call', r'called.*by', r'triggered.*by', r'used.*by',
    ]
    for pattern in _CALLER_PATTERNS:
        if re.search(pattern, query, re.IGNORECASE):
            return "caller"

    # 明确下游意图关键词
    if "implementation" in missing_aspects:
        return "callee"

    # call_chain 无 implementation → 倾向 callee（追实现链更常见）
    return "callee"
```

**说明**：方向推断已基于 query 关键词实现双向区分。上方 `_infer_expansion_direction` 通过 `_CALLER_PATTERNS` 正则列表检测"谁调用"/"被哪里触发"/"来源"等意图，匹配时走 caller 方向，否则默认 callee。

---

## 4. 测试要求

### 4.1 `tests/unit/test_dependency_graph.py`（补充现有或新建）

```python
"""
Unit tests for dependency_graph.py 新增的查询接口。
"""
import pytest
from unittest.mock import patch, MagicMock

from app.services.dependency_graph import get_callees, get_callers


class TestGetCallees:
    @pytest.mark.asyncio
    async def test_returns_callees_from_metadata(self):
        """从 ChromaDB metadata 的 calls 字段提取被调用函数列表。"""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["chunk-1"],
            "metadatas": [{"name": "handle_chat", "calls": "stage1_discovery,stage2_assembly,fuse_query"}],
        }

        with patch("app.services.dependency_graph.get_collection", return_value=mock_collection):
            result = await get_callees("repo1", "handle_chat", "app/services/chat_service.py")

        assert "stage1_discovery" in result
        assert "stage2_assembly" in result
        assert "fuse_query" in result

    @pytest.mark.asyncio
    async def test_empty_file_path_returns_empty(self):
        """file_path 为空时返回空列表（保守策略）。"""
        result = await get_callees("repo1", "handle_chat", None)
        assert result == []

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self):
        """ChromaDB 中无匹配时返回空列表。"""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": [], "metadatas": []}

        with patch("app.services.dependency_graph.get_collection", return_value=mock_collection):
            result = await get_callees("repo1", "nonexistent_func", "some/file.py")

        assert result == []


class TestGetCallers:
    @pytest.mark.asyncio
    async def test_finds_callers_from_all_chunks(self):
        """扫描所有 chunk 的 calls 字段找到调用方。"""
        mock_collection = MagicMock()
        # 第一次调用：验证目标存在
        # 第二次调用：获取所有 chunk
        mock_collection.get.side_effect = [
            {"ids": ["target-chunk"], "metadatas": [{"name": "stage1_discovery"}]},
            {
                "ids": ["caller-1", "caller-2", "other"],
                "metadatas": [
                    {"name": "handle_chat", "calls": "stage1_discovery,apply_budget"},
                    {"name": "handle_stream", "calls": "fuse_query,stage1_discovery"},
                    {"name": "unrelated", "calls": "foo,bar"},
                ],
            },
        ]

        with patch("app.services.dependency_graph.get_collection", return_value=mock_collection):
            result = await get_callers("repo1", "stage1_discovery", "app/services/retriever.py")

        assert "handle_chat" in result
        assert "handle_stream" in result
        assert "unrelated" not in result

    @pytest.mark.asyncio
    async def test_empty_file_path_returns_empty(self):
        result = await get_callers("repo1", "func", None)
        assert result == []
```

---

## 5. 验收标准

1. **`get_callees()` 可从 ChromaDB 查到被调用函数列表**：基于 `calls` metadata 字段
2. **`get_callers()` 可查到调用方**：扫描所有 chunk 的 calls 字段
3. **`file_path` 为空时保守返回空列表**：不做全局查询
4. **`expand_via_dependency_graph()` 返回去重的补充 CodeGuideline 列表**：不重复现有结果
5. **每跳最多 5 个 symbol**：控制 token 膨胀
6. **集成到补检索流程**：仅当 `missing_aspects` 含 `"call_chain"` 或 `"implementation"` 时触发
7. **`source="dep_graph"` 标记正确**
8. **所有测试通过**

---

## 6. 风险与注意事项

- **`get_callers()` 性能 — 硬上限约束**：需要扫描仓库所有 chunk 的 metadatas。硬上限如下：
  - `collection.get(include=["metadatas"])` 只拉 metadatas 不拉 documents（已在代码中体现）
  - 若仓库 chunk 数 > **5000**，`get_callers()` 直接返回空列表并记录 warning：`collection.count()` 预检查，超限跳过
  - 返回结果上限 **10 个**（`list(set(callers))[:10]`，已在代码中体现）
  - `expand_via_dependency_graph` 中每个 guideline 最多处理 **5 个扩展 symbol**（已在代码中体现），总扩展 chunk 上限 **5 个**（已在代码中体现）
  - 整体依赖图扩展超时：在 `chat_service.py` 调用处使用 `asyncio.wait_for(expand_via_dependency_graph(...), timeout=5.0)` 包裹
- **ChromaDB 多键查询**：`get_callees` 使用隐式多键语法（`{"name": ..., "file_path": ...}`），与现有代码风格一致
- **`calls` 字段格式**：`ChunkNode.to_metadata()` 中将 `calls` 存储为逗号分隔字符串（第 41 行：`"calls": ",".join(self.calls)`）。解析时需按逗号 split 并 strip
- **同名函数误匹配**：`expand_via_dependency_graph` 中 `where={"name": rel_sym}` 是全局 name 查询，不含 file_path 限定。常见名称（`create`、`get`、`handle`）可能命中不相关文件中的同名函数。`limit=3` 和总量上限 5 控制影响范围。未来如需精确追踪，需将 `calls` metadata 升级为 `"file_a.py:func_a,file_b.py:func_b"` 格式并重建索引
- **`get_callers` 全量 `collection.get()` 风险**：ChromaDB 底层 SQLite 在大结果集上可能触发 `SQLITE_MAX_VARIABLE_NUMBER` 限制。已通过 `collection.count() > 5000` 预检查跳过大仓库。此外只拉 `metadatas` 不拉 `documents` 降低内存压力
- **`source="dep_graph"` 需要 Plan 04 中 `CodeGuideline.source` 字段已就绪**
