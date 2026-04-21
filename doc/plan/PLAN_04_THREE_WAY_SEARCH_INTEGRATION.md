# Plan 04: Phase 2B — 三路搜索集成

> 优先级：高（Phase 2）
> 前置依赖：Plan 01（Token Budget）、Plan 02（Planner 返回 PlannedTarget）、Plan 03（Code Searcher 模块）
> 预计改动文件数：3
> 策略文档对应：功能二（新增 Grep 层 + 路径搜索层）— 第二部分：集成

---

## 1. 目标

将 Plan 03 创建的 `code_searcher.py` 模块集成到现有检索链路中，实现三路并行检索 + 结果合并。改造后，每次问答的检索阶段同时执行：

1. **向量检索**：现有的 `stage1_discovery()`（语义匹配）
2. **路径搜索**：`search_file_paths()`（文件名 token 匹配）
3. **Grep 搜索**：`grep_codebase()`（精确文本匹配）

三路结果通过新增的 `merge_retrieval_results()` 去重合并后，统一进入 `stage2_assembly()`。

---

## 2. 涉及文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `app/schemas/mcp_types.py` | 修改 | `CodeGuideline` 新增 `source` 字段 |
| `app/services/two_stage_retriever.py` | 修改 | 新增 `merge_retrieval_results()` |
| `app/services/chat_service.py` | 修改 | 三个入口函数改为三路并行 + 合并 |

---

## 3. 详细实施方案

### 3.1 `app/schemas/mcp_types.py` — `CodeGuideline` 新增 `source` 字段

**当前 `CodeGuideline`**（第 6-16 行）：

```python
class CodeGuideline(BaseModel):
    """代码导引：Stage 1 检索结果的轻量摘要"""
    chunk_id: str
    name: str
    file_path: str
    node_type: str
    start_line: int
    end_line: int
    description: str
    relevance_score: float
```

**修改为**：

```python
class CodeGuideline(BaseModel):
    """代码导引：Stage 1 检索结果的轻量摘要"""
    chunk_id: str
    name: str
    file_path: str
    node_type: str
    start_line: int
    end_line: int
    description: str
    relevance_score: float
    source: str = "stage1"  # 来源标记：stage1 / path / grep / dep_graph
```

**影响分析**：
- `source` 有默认值 `"stage1"`，所有现有代码无需修改（向后兼容）
- `stage1_discovery()` 创建的 `CodeGuideline` 自动获得 `source="stage1"`。注意：Stage 1 内部实际混合了三种检索策略（向量 embedding 查询、keyword name 精确匹配、category node_type 过滤），但它们共享同一构造路径且不易拆分。使用 `"stage1"` 而非 `"vector"` 避免暗示这些结果全部来自向量检索
- 新增的路径/grep 结果在创建时显式设置 `source`
- 完整 source 值域：`stage1`（Stage 1 混合检索）、`path`（路径搜索）、`grep`（文本精确匹配）、`dep_graph`（依赖图扩展）

### 3.2 `app/services/two_stage_retriever.py` — 新增 `merge_retrieval_results()`

**在 `stage2_gap_fill_constants()` 之后（文件末尾，第 324 行之后）新增**：

```python
async def merge_retrieval_results(
    vector_guidelines: List[CodeGuideline],
    path_files: List[str],
    grep_matches: List,  # List[GrepMatch]，避免循环导入
    repo_id: str,
) -> List[CodeGuideline]:
    """
    合并三路检索结果：向量检索 + 路径搜索 + Grep 搜索。

    合并策略：
    1. 向量结果保持原样（已有 relevance_score）
    2. 路径匹配的文件：查 ChromaDB 获取对应 chunk，score 设为 0.6
    3. Grep 精确命中：查 ChromaDB 获取包含命中行的 chunk，score 设为 0.75
    4. 去重：相同 file_path + 行范围重叠视为重复，保留分值更高的
    5. 按 relevance_score 降序排列

    返回：
        合并去重后的 CodeGuideline 列表
    """
    # --- 硬上限 ---
    _MAX_PATH_FILES = 10
    _MAX_GREP_MATCHES = 20
    _MAX_MERGED_TOTAL = 50

    path_files = path_files[:_MAX_PATH_FILES]
    grep_matches = grep_matches[:_MAX_GREP_MATCHES]

    # 建立已有结果的索引（用于去重）
    existing_keys = set()  # (file_path, chunk_id) -> 用于去重
    merged = list(vector_guidelines)

    for g in merged:
        existing_keys.add((g.file_path, g.chunk_id))

    collection = get_collection(repo_id)
    _file_chunks_cache: dict = {}  # file_path -> collection.get() 结果缓存

    # --- 合并路径搜索结果 ---
    for file_path in path_files:
        try:
            # 查找该文件下的 chunk
            file_chunks = collection.get(
                where={"file_path": file_path},
                include=["metadatas", "documents", "ids"],
                limit=5,  # 每个文件最多补 5 个 chunk
            )
            if file_chunks["ids"]:
                for i, chunk_id in enumerate(file_chunks["ids"]):
                    key = (file_path, chunk_id)
                    if key in existing_keys:
                        continue
                    metadata = file_chunks["metadatas"][i] if file_chunks["metadatas"] else {}
                    doc = file_chunks["documents"][i] if file_chunks["documents"] else ""
                    first_line = doc.split("\n")[0][:100] if doc else ""
                    merged.append(CodeGuideline(
                        chunk_id=chunk_id,
                        name=metadata.get("name", ""),
                        file_path=file_path,
                        node_type=metadata.get("node_type", ""),
                        start_line=int(metadata.get("start_line", 0)),
                        end_line=int(metadata.get("end_line", 0)),
                        description=first_line,
                        relevance_score=0.6,
                        source="path",
                    ))
                    existing_keys.add(key)
        except Exception as exc:
            logger.debug(f"[Merge] path file lookup failed for {file_path}: {exc}")

    # --- 合并 Grep 搜索结果 ---
    for match in grep_matches:
        file_path = match.file_path
        line_no = match.line_no
        try:
            # 查找包含命中行的 chunk（start_line <= line_no <= end_line）
            # ChromaDB 不支持范围查询，所以查该文件所有 chunk 再过滤
            file_chunks = collection.get(
                where={"file_path": file_path},
                include=["metadatas", "documents", "ids"],
            )
            if file_chunks["ids"]:
                for i, chunk_id in enumerate(file_chunks["ids"]):
                    key = (file_path, chunk_id)
                    if key in existing_keys:
                        continue
                    metadata = file_chunks["metadatas"][i] if file_chunks["metadatas"] else {}
                    sl = int(metadata.get("start_line", 0))
                    el = int(metadata.get("end_line", 0))

                    # 只保留包含命中行的 chunk
                    if sl <= line_no <= el:
                        doc = file_chunks["documents"][i] if file_chunks["documents"] else ""
                        first_line = doc.split("\n")[0][:100] if doc else ""
                        merged.append(CodeGuideline(
                            chunk_id=chunk_id,
                            name=metadata.get("name", ""),
                            file_path=file_path,
                            node_type=metadata.get("node_type", ""),
                            start_line=sl,
                            end_line=el,
                            description=first_line,
                            relevance_score=0.75,  # grep 精确命中，高于路径搜索
                            source="grep",
                        ))
                        existing_keys.add(key)
        except Exception as exc:
            logger.debug(f"[Merge] grep match lookup failed for {file_path}:{line_no}: {exc}")

    # 按 relevance_score 降序排列，截断到硬上限
    merged.sort(key=lambda g: g.relevance_score, reverse=True)
    merged = merged[:_MAX_MERGED_TOTAL]

    logger.debug(
        f"[Merge] repo={repo_id}, vector={len(vector_guidelines)}, "
        f"path_new={sum(1 for g in merged if g.source == 'path')}, "
        f"grep_new={sum(1 for g in merged if g.source == 'grep')}, "
        f"total={len(merged)}"
    )

    return merged
```

**需要在文件顶部新增 import**（如果尚未导入）：

当前 import（第 1-9 行）已包含所有需要的基础导入。`GrepMatch` 不需要在此处导入（参数类型用 `List` 即可，函数内部通过 duck typing 访问 `.file_path` 和 `.line_no`）。

### 3.3 `app/services/chat_service.py` — 三路并行检索

#### 3.3.1 新增 import

在文件顶部（第 9 行附近）新增：

```python
import asyncio
from app.services.code_searcher import search_file_paths, grep_codebase, extract_grep_patterns
```

#### 3.3.2 抽取三路检索为私有函数

在 `_get_codebase_index_text()` 之后、`handle_chat()` 之前新增辅助函数：

```python
async def _three_way_retrieval(
    fused_query: str,
    repo_id: str,
    index_data: Optional[dict],
    repo_dir: Optional[str] = None,
    top_k: int = 20,
) -> tuple:
    """
    三路并行检索：向量 + 路径 + Grep。

    参数:
        repo_dir: 仓库磁盘路径（应从 Repository.local_path 获取）。
                  传递给 grep_codebase。

    返回:
        (merged_guidelines, code_contents, chunk_weights)
    """
    from app.services.two_stage_retriever import merge_retrieval_results

    # 并行执行三路检索
    grep_patterns = extract_grep_patterns(fused_query)

    vector_task = stage1_discovery(fused_query, repo_id, top_k=top_k)
    path_task = search_file_paths(repo_id, fused_query, index_data)
    async def _empty():
        return []

    grep_task = grep_codebase(repo_id, grep_patterns, repo_dir=repo_dir) if grep_patterns else _empty()

    # asyncio.gather 并行执行
    vector_guidelines, path_files, grep_matches = await asyncio.gather(
        vector_task,
        path_task,
        grep_task,
        return_exceptions=True,
    )

    # 处理异常：任一路径失败不影响其他
    if isinstance(vector_guidelines, Exception):
        logger.warning(f"[ThreeWay] vector search failed: {vector_guidelines}")
        vector_guidelines = []
    if isinstance(path_files, Exception):
        logger.debug(f"[ThreeWay] path search failed: {path_files}")
        path_files = []
    if isinstance(grep_matches, Exception):
        logger.debug(f"[ThreeWay] grep search failed: {grep_matches}")
        grep_matches = []

    # 合并三路结果
    merged_guidelines = await merge_retrieval_results(
        vector_guidelines, path_files, grep_matches, repo_id
    )

    # Stage 2：获取完整代码
    top_chunk_ids = [g.chunk_id for g in merged_guidelines[:10]]
    code_contents = await stage2_assembly(top_chunk_ids, repo_id)

    # 构建与 code_contents 位置对齐的权重列表（Plan 01 不变量）
    chunk_weights = [g.relevance_score for g in merged_guidelines[:len(code_contents)]]

    return merged_guidelines, code_contents, chunk_weights
```

#### 3.3.3 修改 `handle_chat()` — 替换单路检索

**当前代码**（第 179-194 行）：

```python
    # 3. Stage 1: 检索导引
    guidelines = await stage1_discovery(fused_query, repo_id, top_k=20)

    # 4. Stage 2: 获取完整代码（选取前 10 个最相关的）
    top_chunk_ids = [g.chunk_id for g in guidelines[:10]]
    code_contents = await stage2_assembly(top_chunk_ids, repo_id)

    # 4.5. Round 2: 从已检索文件中补取遗漏的常量 chunk
    try:
        gap_contents = await stage2_gap_fill_constants(guidelines[:10], repo_id)
        if gap_contents:
            code_contents = gap_contents + code_contents
            logger.info(f"[ChatService] Round 2 gap-fill: 补取 {len(gap_contents)} 个常量 chunk")
    except Exception as _gf:
        logger.debug(f"[ChatService] gap-fill failed (non-fatal): {_gf}")
```

**修改为**：

```python
    # 3. 三路并行检索（向量 + 路径 + Grep）
    index_data = None
    if codebase_index_text:
        try:
            from app.models.repo_index import RepoIndex
            repo_index = await db.get(RepoIndex, repo_id)
            if repo_index:
                index_data = repo_index.index_json
        except Exception:
            pass

    # 获取仓库磁盘路径（Repository.local_path 为权威来源）
    from app.models.repository import Repository
    repo_obj = await db.get(Repository, repo_id)
    repo_dir = repo_obj.local_path if repo_obj and repo_obj.local_path else None

    guidelines, code_contents, chunk_weights = await _three_way_retrieval(
        fused_query, repo_id, index_data, repo_dir=repo_dir, top_k=20
    )

    # 3.5. Round 2: 从已检索文件中补取遗漏的常量 chunk
    try:
        gap_contents = await stage2_gap_fill_constants(guidelines[:10], repo_id)
        if gap_contents:
            code_contents = gap_contents + code_contents
            chunk_weights = [0.7] * len(gap_contents) + chunk_weights  # 权重同步（Plan 01 不变量）
            logger.info(f"[ChatService] Round 2 gap-fill: 补取 {len(gap_contents)} 个常量 chunk")
    except Exception as _gf:
        logger.debug(f"[ChatService] gap-fill failed (non-fatal): {_gf}")
```

**注意**：`index_data` 的获取需要 `db` session 和 `RepoIndex` 模型。当前 `_get_codebase_index_text()` 已经做了类似的查询（第 130-140 行），为避免重复查询，可以在调用 `_get_codebase_index_text` 时同时缓存 `index_data`。

**优化方案**：修改 `_get_codebase_index_text` 使其同时返回 `index_data`：

```python
async def _get_codebase_index(db: AsyncSession, repo_id: str) -> tuple:
    """返回 (codebase_index_text, index_data)"""
    try:
        from app.models.repo_index import RepoIndex
        from app.services.codebase_indexer import format_codebase_index
        repo_index = await db.get(RepoIndex, repo_id)
        if repo_index and repo_index.index_json:
            return format_codebase_index(repo_index.index_json), repo_index.index_json
    except Exception as e:
        logger.debug(f"[ChatService] 无法加载代码库索引: {e}")
    return None, None
```

然后在三个入口函数中将 `codebase_index_text = await _get_codebase_index_text(db, repo_id)` 替换为 `codebase_index_text, index_data = await _get_codebase_index(db, repo_id)`。

**关键时序调整**：当前 `_get_codebase_index_text` 的调用位于 Stage 1 检索之后（用于构建 system\_prompt）。但三路检索需要 `index_data` 作为路径搜索的输入，因此 `_get_codebase_index` 的调用必须**移到三路检索之前**。具体做法：在三个入口函数中，将 `_get_codebase_index` 调用提到 `fuse_query` 之后、`_three_way_retrieval` 之前。system\_prompt 的构建仍使用返回的 `codebase_index_text`。

#### 3.3.4 修改 `handle_chat_stream()` — 同样替换

**当前代码**（第 315-327 行）：

```python
    # 3. Stage 1 + Stage 2 检索
    guidelines = await stage1_discovery(fused_query, repo_id, top_k=20)
    top_chunk_ids = [g.chunk_id for g in guidelines[:10]]
    code_contents = await stage2_assembly(top_chunk_ids, repo_id)

    # 3.5. Round 2 ...
```

**替换为同样的三路检索调用**（模式同 3.3.3）。

#### 3.3.5 修改 `handle_deep_research_stream()` — 同样替换

**当前代码**（第 466-478 行）：

```python
    # 5. 双阶段 RAG 检索
    guidelines = await stage1_discovery(fused_query, repo_id, top_k=20)
    top_chunk_ids = [g.chunk_id for g in guidelines[:10]]
    code_contents = await stage2_assembly(top_chunk_ids, repo_id)

    # 5.5. Round 2 ...
```

**替换为同样的三路检索调用**。

---

## 4. 测试要求

在 `tests/unit/test_code_searcher.py`（Plan 03 已创建）中补充集成验证：

```python
class TestMergeRetrievalResults:
    """
    验证 merge_retrieval_results 的去重和排序行为。
    需要 mock ChromaDB collection。
    """

    @pytest.mark.asyncio
    async def test_dedup_by_file_path_and_chunk_id(self):
        """相同 file_path + chunk_id 的结果不重复。"""
        from app.schemas.mcp_types import CodeGuideline, GrepMatch

        vector_guidelines = [
            CodeGuideline(
                chunk_id="chunk-1", name="func_a", file_path="a.py",
                node_type="function_definition", start_line=1, end_line=10,
                description="func_a", relevance_score=0.9, source="stage1",
            ),
        ]
        # grep 命中同一文件同一 chunk
        grep_matches = [GrepMatch(file_path="a.py", line_no=5, line_content="x", context_lines=[])]

        # mock collection.get 返回同一个 chunk-1
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["chunk-1"],
            "metadatas": [{"name": "func_a", "file_path": "a.py", "node_type": "function_definition",
                           "start_line": 1, "end_line": 10}],
            "documents": ["def func_a(): pass"],
        }

        with patch("app.services.two_stage_retriever.get_collection", return_value=mock_collection):
            result = await merge_retrieval_results(vector_guidelines, [], grep_matches, "repo1")

        chunk_ids = [g.chunk_id for g in result]
        assert chunk_ids.count("chunk-1") == 1  # 无重复

    @pytest.mark.asyncio
    async def test_grep_score_higher_than_path(self):
        """grep 结果（0.75）分值高于路径结果（0.6）。"""
        from app.schemas.mcp_types import CodeGuideline, GrepMatch

        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["chunk-new"],
            "metadatas": [{"name": "func_b", "file_path": "b.py", "node_type": "function_definition",
                           "start_line": 1, "end_line": 10}],
            "documents": ["def func_b(): pass"],
        }

        grep_matches = [GrepMatch(file_path="b.py", line_no=5, line_content="x", context_lines=[])]

        with patch("app.services.two_stage_retriever.get_collection", return_value=mock_collection):
            result = await merge_retrieval_results([], ["b.py"], grep_matches, "repo1")

        grep_items = [g for g in result if g.source == "grep"]
        path_items = [g for g in result if g.source == "path"]
        if grep_items and path_items:
            assert grep_items[0].relevance_score > path_items[0].relevance_score

    @pytest.mark.asyncio
    async def test_empty_path_and_grep_returns_vector_only(self):
        """路径和 grep 结果为空时，返回纯向量结果。"""
        from app.schemas.mcp_types import CodeGuideline

        vector = [CodeGuideline(
            chunk_id="v1", name="f", file_path="x.py",
            node_type="function_definition", start_line=1, end_line=5,
            description="", relevance_score=0.8, source="stage1",
        )]

        mock_collection = MagicMock()
        with patch("app.services.two_stage_retriever.get_collection", return_value=mock_collection):
            result = await merge_retrieval_results(vector, [], [], "repo1")

        assert len(result) == 1
        assert result[0].source == "stage1"
```

---

## 5. 验收标准

1. **三路并行执行**：`asyncio.gather` 并行调用三个检索函数
2. **任一路径失败不影响其他**：单路异常被捕获并降级为空结果
3. **去重正确**：相同 `(file_path, chunk_id)` 不重复出现
4. **`source` 字段标记正确**：Stage 1 结果为 `"stage1"`，路径为 `"path"`，grep 为 `"grep"`
5. **三个入口统一改造**：`handle_chat`、`handle_chat_stream`、`handle_deep_research_stream`
6. **现有测试不 break**：`pytest tests/ -v` 原有测试全部通过
7. **gap_fill 仍然工作**：常量补洞逻辑在三路检索之后照常执行

---

## 6. 风险与注意事项

- **`index_data` 获取时机**：需要在检索阶段就拿到 `RepoIndex.index_json`（原始 dict），而非仅 `format_codebase_index()` 的文本。建议修改 `_get_codebase_index_text` 为同时返回两者
- **ChromaDB 查询频率增加 — 硬上限约束**：`merge_retrieval_results` 中每个路径文件和 grep 匹配都会查 ChromaDB。硬上限如下：
  - `path_files` 处理上限：**最多 10 个文件**（`path_files[:10]`），每个文件 `limit=5` chunk → 最多 10 次 ChromaDB 查询
  - `grep_matches` 处理上限：**最多 20 条匹配**（`grep_matches[:20]`），按 `file_path` 去重后批量查询 → 最多 20 次查询（同一文件只查一次，缓存 `file_chunks`）
  - 合并后总 `merged` 列表上限：**最多 50 条 CodeGuideline**（超出截断）
  - 实现要点：在 `merge_retrieval_results` 入口处对 `path_files` 和 `grep_matches` 做截断，并在循环中用 `file_chunks_cache: Dict[str, dict]` 缓存按 file_path 查询的结果，避免同一文件重复查 ChromaDB
- **`asyncio.gather` 中的 grep_codebase**：`grep_codebase` 内部使用 `subprocess.run`（同步），虽然被 `async def` 包装但实际仍是阻塞调用。如果 grep 耗时较长，可能阻塞事件循环。解决方案：使用 `asyncio.to_thread()` 包装 `_grep_with_python` 和 `_grep_with_rg` 的调用
- **`extract_grep_patterns` 暴露为 public API**：需要在 `chat_service.py` 中导入。当前以 `_` 前缀命名为私有函数。建议去掉下划线或新建 public 包装函数
