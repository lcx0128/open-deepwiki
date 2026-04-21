# Plan 02: Phase 1B — Planner 定点读取升级

> 优先级：高（Phase 1，与 Plan 01 并行）
> 前置依赖：Plan 00（测试基线）
> 预计改动文件数：3
> 策略文档对应：功能五（Planner 从文件级读取升级为定点读取）

---

## 1. 目标

将检索规划器（planner）从"返回文件路径 → 读取前 300 行"升级为"返回文件路径+目标 symbol → 基于 symbol 位置做窗口读取"。解决当前关键函数在文件中后部（如第 400+ 行）被固定行数截断遗漏的问题。

**改造前行为**：`plan_retrieval()` 返回 `List[str]`（纯文件路径），`chat_service.py` 中 `read_file_context(repo_id, planned_fp, 1, 300)` 固定读取前 300 行。

**改造后行为**：`plan_retrieval()` 返回 `List[PlannedTarget]`（含 `file_path` + `symbol_name`），使用 `read_targeted_context()` 根据 symbol 在 ChromaDB 中的 `start_line`/`end_line` 做窗口读取。无 symbol 时退回前 300 行（向后兼容）。

---

## 2. 涉及文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `app/services/retrieval_planner.py` | 修改 | 新增 `PlannedTarget`；升级 `PLANNER_PROMPT`；`plan_retrieval()` 返回类型变更 |
| `app/services/two_stage_retriever.py` | 修改 | 新增 `read_targeted_context()` |
| `app/services/chat_service.py` | 修改 | 三处 planner 调用段适配新返回类型 |

---

## 3. 详细实施方案

### 3.1 `app/services/retrieval_planner.py` — 升级输出结构

#### 3.1.1 新增 `PlannedTarget` 数据类

在文件顶部（第 9 行 import 区之后，`_BROAD_PATTERNS` 之前，约第 15 行位置）新增：

```python
from dataclasses import dataclass
from typing import Optional, List

@dataclass
class PlannedTarget:
    """检索规划目标：文件路径 + 可选的 symbol 名称"""
    file_path: str
    symbol_name: Optional[str] = None
```

#### 3.1.2 升级 `PLANNER_PROMPT`

**当前 PLANNER_PROMPT**（第 25-39 行）：

```
Return ONLY a JSON array of file paths ...
Example: ["app/services/chat_service.py", "app/services/wiki_generator.py"]
```

**修改为**：

```python
PLANNER_PROMPT = """\
You are a code retrieval expert. Given a codebase index and a user question, \
identify which specific files and symbols are most likely to contain the answer.

CODEBASE INDEX:
{codebase_index}

USER QUESTION: {question}

Return ONLY a JSON array of objects with "file" (required) and "symbol" (optional) fields. \
"symbol" should be the most relevant function, class, or constant name in that file. \
Return at most 5 items. Return [] if no files are clearly relevant.

Example: [
  {{"file": "app/services/chat_service.py", "symbol": "handle_chat_stream"}},
  {{"file": "app/services/wiki_generator.py", "symbol": null}}
]

Response (JSON array only, no explanation):"""
```

**注意**：`{{` 和 `}}` 是因为在 Python f-string 或 `.format()` 中需要转义花括号。当前使用 `.format()`，需要双花括号。

#### 3.1.3 修改 `plan_retrieval()` 返回类型

**当前签名**（第 50-55 行）：

```python
async def plan_retrieval(
    query: str,
    codebase_index_text: str,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
) -> list:
```

**修改为**：

```python
async def plan_retrieval(
    query: str,
    codebase_index_text: str,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
) -> List[PlannedTarget]:
```

**修改解析逻辑**（第 77-82 行）：

当前：

```python
        match = re.search(r'\[.*?\]', content, re.DOTALL)
        if match:
            file_paths = json.loads(match.group())
            if isinstance(file_paths, list):
                return [fp for fp in file_paths if isinstance(fp, str)][:5]
```

修改为：

```python
        match = re.search(r'\[.*?\]', content, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            if isinstance(parsed, list):
                targets = []
                for item in parsed[:5]:
                    if isinstance(item, str):
                        # 向后兼容：纯字符串格式
                        targets.append(PlannedTarget(file_path=item))
                    elif isinstance(item, dict) and "file" in item:
                        targets.append(PlannedTarget(
                            file_path=item["file"],
                            symbol_name=item.get("symbol"),
                        ))
                return targets
```

**返回空列表时的类型**：最后第 85 行 `return []` 不需要修改，`List[PlannedTarget]` 的空列表仍然是 `[]`。

### 3.2 `app/services/two_stage_retriever.py` — 新增 `read_targeted_context()`

**在 `read_file_context()` 函数之后（第 273 行之后）新增**：

```python
async def read_targeted_context(
    repo_id: str,
    target,  # PlannedTarget，避免循环导入用 duck typing
) -> str:
    """
    基于 PlannedTarget 做定点读取。

    若 target.symbol_name 存在：
        1. 在 ChromaDB 中精确查该 symbol 的 start_line / end_line
        2. 读取 [start_line - 10, end_line + 30] 窗口（含上下文缓冲）
    若无 symbol 或查询失败：
        fallback 到 read_file_context(repo_id, file_path, 1, 300)

    返回格式与 planner 注入一致：
        "// [TARGETED FILE] file_path (symbol_name)\n{content}"
    """
    file_path = target.file_path
    symbol_name = getattr(target, 'symbol_name', None)

    if symbol_name:
        try:
            collection = get_collection(repo_id)
            # Best-effort 查询：name + file_path 定位（同文件同名时取首条）
            symbol_results = collection.get(
                where={"name": symbol_name, "file_path": file_path},
                include=["metadatas"],
                limit=5,  # 取多条以便按 parent_name 消歧
            )

            if symbol_results["ids"] and symbol_results["metadatas"]:
                # 如果命中多条（同文件内 __init__、create 等常见名），
                # 优先选 parent_name 非空的（类方法优先于模块级函数）
                metadata = symbol_results["metadatas"][0]
                if len(symbol_results["ids"]) > 1:
                    for m in symbol_results["metadatas"]:
                        if m.get("parent_name"):
                            metadata = m
                            break

                start_line = int(metadata.get("start_line", 0))
                end_line = int(metadata.get("end_line", 0))

                if start_line > 0 and end_line > 0:
                    # 扩展窗口：前 10 行（import/decorator 等上下文），后 30 行（函数体完整）
                    window_start = max(1, start_line - 10)
                    window_end = end_line + 30
                    fc = read_file_context(repo_id, file_path, window_start, window_end)
                    label = f"// [TARGETED FILE] {file_path} ({symbol_name}, Lines {window_start}-{window_end})"
                    return f"{label}\n{fc.content}"
        except Exception as exc:
            logger.debug(f"[TargetedRead] symbol lookup failed for {symbol_name}@{file_path}: {exc}")

    # Fallback：无 symbol 或查询失败，读前 300 行
    try:
        fc = read_file_context(repo_id, file_path, 1, 300)
        return f"// [TARGETED FILE] {file_path}\n{fc.content}"
    except Exception as exc:
        logger.debug(f"[TargetedRead] file read failed for {file_path}: {exc}")
        return ""
```

**ChromaDB 查询说明（best-effort 定位）**：
- 使用隐式多键 `where` 语法（`{"name": ..., "file_path": ...}`），与现有代码风格一致
- `name + file_path` **不保证唯一**：同一文件中 `__init__`、`create`、`handle` 等常见方法名可能出现在多个类中
- `limit=5` 取多条候选，通过 `parent_name` 字段消歧（当前 metadata 已存储 `parent_name`，见 `chunk_node.py` 第 39 行）
- 若仍有多个匹配（无 parent_name 或 parent_name 相同），取第一条。这是 best-effort 行为，不是精确定位
- 最坏情况：读到同名函数的窗口（位于错误的类中），但由于同文件内，代码仍然相关，影响有限

### 3.3 `app/services/chat_service.py` — 三处 planner 调用适配

#### 3.3.1 `handle_chat()` — 第 202-211 行

**当前代码**：

```python
    # 检索规划：对宽泛查询，使用 LLM 识别目标文件并直接读取
    if codebase_index_text and is_broad_query(fused_query):
        planned_files = await plan_retrieval(fused_query, codebase_index_text, llm_provider, llm_model)
        for planned_fp in planned_files[:3]:  # 最多 3 个文件，控制 token
            try:
                fc = read_file_context(repo_id, planned_fp, 1, 300)  # 前 300 行
                code_contents.insert(0, f"// [TARGETED FILE] {planned_fp}\n{fc.content}")
                logger.info(f"[ChatService] 规划检索追加文件: {planned_fp}")
            except Exception as _fe:
                logger.debug(f"[ChatService] 规划文件读取失败 {planned_fp}: {_fe}")
```

**修改为**：

```python
    # 检索规划：对宽泛查询，使用 LLM 识别目标文件并定点读取
    if codebase_index_text and is_broad_query(fused_query):
        planned_targets = await plan_retrieval(fused_query, codebase_index_text, llm_provider, llm_model)
        for target in planned_targets[:3]:  # 最多 3 个文件，控制 token
            try:
                content = await read_targeted_context(repo_id, target)
                if content:
                    code_contents.insert(0, content)
                    logger.info(f"[ChatService] 规划检索追加: {target.file_path} (symbol={target.symbol_name})")
            except Exception as _fe:
                logger.debug(f"[ChatService] 规划文件读取失败 {target.file_path}: {_fe}")
```

#### 3.3.2 `handle_chat_stream()` — 第 337-345 行

**同样的模式修改**：

当前：

```python
    if codebase_index_text and is_broad_query(fused_query):
        planned_files = await plan_retrieval(fused_query, codebase_index_text, llm_provider, llm_model)
        for planned_fp in planned_files[:3]:
            try:
                fc = read_file_context(repo_id, planned_fp, 1, 300)
                code_contents.insert(0, f"// [TARGETED FILE] {planned_fp}\n{fc.content}")
                logger.info(f"[ChatService] 规划检索追加文件: {planned_fp}")
            except Exception as _fe:
                logger.debug(f"[ChatService] 规划文件读取失败 {planned_fp}: {_fe}")
```

修改为与 3.3.1 相同的 `planned_targets` + `read_targeted_context` 模式。

#### 3.3.3 `handle_deep_research_stream()` — 第 487-495 行

**同样的模式修改**：

当前：

```python
    if codebase_index_text and (is_first or is_broad_query(fused_query)):
        planned_files = await plan_retrieval(fused_query, codebase_index_text, llm_provider, llm_model)
        for planned_fp in planned_files[:3]:
            try:
                fc = read_file_context(repo_id, planned_fp, 1, 300)
                code_contents.insert(0, f"// [TARGETED FILE] {planned_fp}\n{fc.content}")
                logger.info(f"[DeepResearch] 规划检索追加文件: {planned_fp}")
            except Exception as _fe:
                logger.debug(f"[DeepResearch] 规划文件读取失败 {planned_fp}: {_fe}")
```

修改为同样的 `planned_targets` + `read_targeted_context` 模式。注意条件判断 `(is_first or is_broad_query(fused_query))` 保持不变。

### 3.4 `chat_service.py` 顶部 import 调整

**当前第 9 行**：

```python
from app.services.two_stage_retriever import stage1_discovery, stage2_assembly, read_file_context, stage2_gap_fill_constants
```

**修改为**：

```python
from app.services.two_stage_retriever import (
    stage1_discovery, stage2_assembly, read_file_context,
    read_targeted_context, stage2_gap_fill_constants,
)
```

注意：`read_file_context` 的导入可以保留（其他地方可能间接使用），也可以在确认无其他调用后移除。

---

## 4. 测试要求

### 4.1 `tests/unit/test_retrieval_planner.py`（新建）

```python
"""
Unit tests for app/services/retrieval_planner.py
验证 PlannedTarget 返回格式和新旧 JSON 格式兼容性。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.retrieval_planner import plan_retrieval, PlannedTarget, is_broad_query


class TestPlannedTargetParsing:
    @pytest.mark.asyncio
    async def test_new_format_returns_planned_targets(self):
        """LLM 返回新格式（对象数组）时解析为 PlannedTarget 列表。"""
        mock_response = MagicMock()
        mock_response.content = '[{"file": "app/main.py", "symbol": "app"}]'

        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(return_value=mock_response)

        with patch("app.services.retrieval_planner.create_adapter", return_value=mock_adapter):
            result = await plan_retrieval("question", "index text")

        assert len(result) == 1
        assert isinstance(result[0], PlannedTarget)
        assert result[0].file_path == "app/main.py"
        assert result[0].symbol_name == "app"

    @pytest.mark.asyncio
    async def test_old_format_backward_compatible(self):
        """LLM 返回旧格式（字符串数组）时仍然正确解析。"""
        mock_response = MagicMock()
        mock_response.content = '["app/main.py", "app/config.py"]'

        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(return_value=mock_response)

        with patch("app.services.retrieval_planner.create_adapter", return_value=mock_adapter):
            result = await plan_retrieval("question", "index text")

        assert len(result) == 2
        assert result[0].file_path == "app/main.py"
        assert result[0].symbol_name is None

    @pytest.mark.asyncio
    async def test_symbol_null_parsed_as_none(self):
        """symbol 为 null 时解析为 None。"""
        mock_response = MagicMock()
        mock_response.content = '[{"file": "app/main.py", "symbol": null}]'

        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(return_value=mock_response)

        with patch("app.services.retrieval_planner.create_adapter", return_value=mock_adapter):
            result = await plan_retrieval("question", "index text")

        assert result[0].symbol_name is None

    @pytest.mark.asyncio
    async def test_max_5_targets(self):
        """最多返回 5 个目标。"""
        items = [{"file": f"file_{i}.py", "symbol": None} for i in range(10)]
        mock_response = MagicMock()
        mock_response.content = str(items).replace("'", '"').replace("None", "null")

        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(return_value=mock_response)

        with patch("app.services.retrieval_planner.create_adapter", return_value=mock_adapter):
            result = await plan_retrieval("question", "index text")

        assert len(result) <= 5

    @pytest.mark.asyncio
    async def test_error_returns_empty_list(self):
        """LLM 调用失败时返回空列表。"""
        mock_adapter = MagicMock()
        mock_adapter.generate_with_rate_limit = AsyncMock(side_effect=RuntimeError("timeout"))

        with patch("app.services.retrieval_planner.create_adapter", return_value=mock_adapter):
            result = await plan_retrieval("question", "index text")

        assert result == []


class TestIsBroadQuery:
    def test_chinese_broad_keywords(self):
        assert is_broad_query("列出所有的 prompt 常量") is True
        assert is_broad_query("全部的路由定义在哪") is True

    def test_english_broad_keywords(self):
        assert is_broad_query("list all API endpoints") is True
        assert is_broad_query("find all constants") is True

    def test_non_broad_query(self):
        assert is_broad_query("handle_chat 怎么工作的") is False
```

---

## 5. 验收标准

1. **`plan_retrieval()` 返回 `List[PlannedTarget]`**：新旧 JSON 格式均能正确解析
2. **`read_targeted_context()` 存在且可调用**：接受 `PlannedTarget` 输入
3. **symbol 定位生效**：当 ChromaDB 中存在匹配 symbol 时，读取范围为 `[start_line-10, end_line+30]`，而非固定 `[1, 300]`
4. **向后兼容**：无 symbol 或 ChromaDB 查询失败时退回 `read_file_context(repo_id, file_path, 1, 300)`
5. **三个入口统一改造**：`handle_chat`、`handle_chat_stream`、`handle_deep_research_stream` 都使用 `read_targeted_context`
6. **所有新测试通过**：`pytest tests/unit/test_retrieval_planner.py -v`

---

## 6. 风险与注意事项

- **ChromaDB 多键查询兼容性**：使用隐式多键 `where` 语法，与现有代码风格一致，兼容 ChromaDB >= 0.4.0
- **`read_targeted_context` 是 async**：因为需要调用 ChromaDB（`get_collection` 是同步的但查询可能需要 I/O），但 `read_file_context` 是同步函数。需确认 ChromaDB `collection.get()` 在异步上下文中的行为
- **PLANNER_PROMPT 变更可能影响 LLM 输出格式**：旧版 LLM 可能仍然返回纯字符串数组，因此解析逻辑必须兼容两种格式
- **窗口读取行数扩大**：从固定 300 行变为可能更少（如某个函数只有 30 行），也可能更多（`end_line + 30` 可能超过 300），需要确保 token budget 阶段（Plan 01）能处理
