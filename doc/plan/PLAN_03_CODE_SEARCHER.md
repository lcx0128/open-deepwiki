# Plan 03: Phase 2A — 新增 Code Searcher 模块（路径搜索 + Grep 搜索）

> 优先级：高（Phase 2）
> 前置依赖：Plan 00（测试基线）
> 预计改动文件数：3（均为新增文件）
> 策略文档对应：功能二（新增 Grep 层 + 路径搜索层）— 第一部分：独立模块

---

## 1. 目标

新建 `app/services/code_searcher.py` 模块，实现两个独立搜索函数：

1. **`search_file_paths()`**：根据 query 关键词在 `RepoIndex` 的文件路径中做 token 级匹配
2. **`grep_codebase()`**：对克隆到本地的代码仓库做文本级精确搜索

本 Plan 只创建独立模块和对应测试，不与 `chat_service.py` 或 `two_stage_retriever.py` 集成。集成工作在 Plan 04 中完成。

**核心约束**：本项目明确支持 Windows 部署（Celery `--pool=solo`、rmtree 只读修复等），因此 `grep_codebase()` 必须实现双路径：优先 `rg`（ripgrep），不可用时自动降级为 Python 原生搜索。Python fallback 是正式兼容路径，不是可选优化。

---

## 2. 涉及文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `app/services/code_searcher.py` | 新增 | 路径搜索 + grep 搜索主模块 |
| `app/schemas/mcp_types.py` | 修改 | 新增 `GrepMatch` schema |
| `tests/unit/test_code_searcher.py` | 新增 | 路径搜索和 grep 搜索的单元测试 |

---

## 3. 详细实施方案

### 3.1 `app/schemas/mcp_types.py` — 新增 `GrepMatch`

**在文件末尾（第 25 行之后）新增**：

```python
class GrepMatch(BaseModel):
    """Grep 搜索结果：精确文本匹配的位置和上下文"""
    file_path: str         # 相对于仓库根目录的文件路径
    line_no: int           # 命中行号（1-indexed）
    line_content: str      # 命中行的完整内容
    context_lines: List[str] = []  # 命中行前后的上下文行
```

需要在 `mcp_types.py` 的 import 中补充 `List`：

当前第 3 行：`from typing import Optional`

修改为：`from typing import List, Optional`

### 3.2 `app/services/code_searcher.py` — 新增文件

```python
"""
code_searcher.py — 路径搜索 + Grep 搜索

提供两个独立搜索函数：
1. search_file_paths: 基于 RepoIndex 的文件路径 token 级匹配
2. grep_codebase: 基于本地克隆仓库的文本精确搜索

设计原则：
- 不调用 LLM，纯内存/磁盘操作
- grep 优先使用系统 rg（ripgrep），不可用时自动降级为 Python 原生搜索
- Windows 兼容：Python fallback 是正式兼容路径
"""
import logging
import os
import re
import subprocess
from typing import List, Optional

from app.schemas.mcp_types import GrepMatch
from app.config import settings

logger = logging.getLogger(__name__)
```

#### 3.2.1 `search_file_paths()`

```python
# --- 路径 token 化 ---

def _tokenize_path(path: str) -> List[str]:
    """
    将文件路径拆分为可搜索的 token 列表。

    支持：
    - 路径分隔符拆分: "app/services/chat_service.py" → ["app", "services", "chat_service", "py"]
    - CamelCase 拆分: "ChatService" → ["chat", "service"]
    - snake_case 拆分: "chat_service" → ["chat", "service"]
    - 全部小写化
    """
    # 按路径分隔符和 . 拆分
    parts = re.split(r'[/\\.]', path)
    tokens = []
    for part in parts:
        if not part:
            continue
        # CamelCase 拆分
        camel_parts = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|[0-9]+', part)
        if camel_parts:
            tokens.extend(t.lower() for t in camel_parts)
        else:
            tokens.append(part.lower())
    return tokens


def _tokenize_query(query: str) -> List[str]:
    """
    将用户查询拆分为搜索 token。

    支持：
    - CamelCase 标识符拆分
    - snake_case 标识符拆分
    - 普通空格拆分
    - 过滤短词和常见停用词
    """
    STOP_WORDS = {
        "the", "and", "for", "with", "that", "this", "from", "have", "will",
        "what", "how", "can", "are", "was", "but", "not", "all", "been",
        "has", "its", "into", "than", "then", "who", "did", "get", "may",
        "new", "one", "our", "out", "say", "where", "which", "file",
        "在", "的", "了", "是", "有", "和", "与", "这", "那", "吗", "呢",
        "哪", "什么", "怎么", "如何", "为什么", "哪里", "哪个",
    }

    # 提取所有"词"（包含 CamelCase、snake_case、路径片段）
    raw_tokens = re.findall(
        r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|[a-z][a-z0-9_]*(?:_[a-z0-9]+)+|[A-Z][A-Z0-9_]+|[\u4e00-\u9fff]+',
        query,
    )
    tokens = []
    for t in raw_tokens:
        lower_t = t.lower()
        if len(lower_t) < 2 or lower_t in STOP_WORDS:
            continue
        tokens.append(lower_t)
    return tokens


async def search_file_paths(
    repo_id: str,
    query: str,
    index_data: Optional[dict] = None,
) -> List[str]:
    """
    在 RepoIndex 的文件路径中做 token 级匹配。

    参数:
        repo_id: 仓库 ID
        query: 用户查询
        index_data: 可选，直接传入 RepoIndex.index_json（避免重复 DB 查询）

    返回:
        匹配的文件相对路径列表，按匹配度降序排列，最多 10 条

    不调用 LLM，纯内存操作。
    """
    if index_data is None:
        # 从数据库加载（需要异步 session，由调用方传入更佳）
        # 但为保持接口简洁，此处允许传入 index_data
        logger.warning("[PathSearch] index_data 为空，跳过路径搜索")
        return []

    query_tokens = _tokenize_query(query)
    if not query_tokens:
        return []

    # 评分：对每个文件路径计算 token 匹配度
    scored_paths = []
    for file_path in index_data.keys():
        path_tokens = _tokenize_path(file_path)
        if not path_tokens:
            continue

        # 计算匹配 token 数
        path_token_set = set(path_tokens)
        match_count = sum(1 for qt in query_tokens if qt in path_token_set)

        if match_count > 0:
            score = match_count / max(len(query_tokens), 1)
            scored_paths.append((file_path, score))

    # 按匹配度降序排列
    scored_paths.sort(key=lambda x: x[1], reverse=True)
    return [fp for fp, _ in scored_paths[:10]]
```

#### 3.2.2 `grep_codebase()`

```python
# --- Grep 搜索 ---

# 过滤目录列表（rg 和 Python fallback 均需过滤）
_EXCLUDED_DIRS = {
    '.git', 'node_modules', '__pycache__', '.venv', 'venv',
    '.tox', '.eggs', 'dist', 'build', '.mypy_cache', '.pytest_cache',
}

# 过滤文件扩展名（二进制文件）
_BINARY_EXTENSIONS = {
    '.pyc', '.pyo', '.so', '.dll', '.exe', '.bin', '.jar', '.class',
    '.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg', '.woff', '.woff2',
    '.ttf', '.eot', '.pdf', '.zip', '.tar', '.gz', '.bz2', '.7z',
    '.mp3', '.mp4', '.avi', '.mov', '.db', '.sqlite', '.sqlite3',
}


def _is_binary_file(file_path: str) -> bool:
    """根据扩展名判断是否为二进制文件"""
    _, ext = os.path.splitext(file_path)
    return ext.lower() in _BINARY_EXTENSIONS


def _should_skip_dir(dir_name: str) -> bool:
    """判断目录是否应跳过"""
    return dir_name in _EXCLUDED_DIRS


def _check_rg_available() -> bool:
    """检测系统是否安装了 ripgrep (rg)"""
    try:
        result = subprocess.run(
            ["rg", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def extract_grep_patterns(query: str) -> List[str]:
    """
    从用户查询中提取 grep 搜索模式。

    策略（纯规则，不调用 LLM）：
    1. 引号内的字面量：如 "MultipleResultsFound"、'/api/repositories'
    2. CamelCase 标识符：如 ChatService、FileState
    3. snake_case 标识符：如 handle_chat_stream、apply_token_budget
    4. ALL_CAPS 常量：如 BUDGET_RATIO、MAX_CONCURRENT
    5. 路由路径：如 /api/chat、/api/wiki/{repo_id}
    """
    patterns = []

    # 1. 引号内字面量
    quoted = re.findall(r'["\']([^"\']{2,})["\']', query)
    patterns.extend(quoted)

    # 2. CamelCase 标识符（至少 2 个大写开头部分）
    camel = re.findall(r'\b([A-Z][a-z]+(?:[A-Z][a-z0-9]+)+)\b', query)
    patterns.extend(camel)

    # 3. snake_case 标识符（至少含一个下划线）
    snake = re.findall(r'\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b', query)
    patterns.extend(snake)

    # 4. ALL_CAPS 常量（至少含一个下划线且全大写）
    caps = re.findall(r'\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\b', query)
    patterns.extend(caps)

    # 5. 路由路径（以 / 开头的路径样式字符串）
    routes = re.findall(r'(/[a-zA-Z0-9_/{}]+)', query)
    patterns.extend(routes)

    # 去重并保持顺序
    seen = set()
    unique = []
    for p in patterns:
        if p not in seen and len(p) >= 2:
            seen.add(p)
            unique.append(p)

    return unique[:10]  # 最多 10 个模式


def _grep_with_rg(
    repo_dir: str,
    patterns: List[str],
    context_lines: int = 3,
    max_results_per_pattern: int = 10,
) -> List[GrepMatch]:
    """使用 ripgrep 执行搜索"""
    results = []

    for pattern in patterns:
        try:
            cmd = [
                "rg",
                "--no-heading",
                "--line-number",
                "--max-count", str(max_results_per_pattern),
                "--context", str(context_lines),
                "--type-not", "binary",
            ]
            # 添加排除目录
            for excluded in _EXCLUDED_DIRS:
                cmd.extend(["--glob", f"!{excluded}/"])

            cmd.extend([pattern, repo_dir])

            proc = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=30,
                encoding="utf-8", errors="replace",
            )

            if proc.returncode == 0 and proc.stdout:
                results.extend(
                    _parse_rg_output(proc.stdout, repo_dir, context_lines)
                )
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.debug(f"[Grep/rg] pattern '{pattern}' failed: {exc}")

    return results


def _parse_rg_output(output: str, repo_dir: str, context_lines: int) -> List[GrepMatch]:
    """
    解析 rg --no-heading --line-number 输出格式。
    格式：file_path:line_no:line_content（匹配行）
    格式：file_path-line_no-line_content（上下文行）
    """
    matches = []
    current_context = []

    for line in output.strip().split("\n"):
        if not line.strip() or line == "--":
            if current_context:
                current_context = []
            continue

        # 匹配行：path:line_no:content
        m = re.match(r'^(.+?):(\d+):(.*)$', line)
        if m:
            file_path = m.group(1)
            # 转为相对路径
            if file_path.startswith(repo_dir):
                file_path = os.path.relpath(file_path, repo_dir)
            file_path = file_path.replace("\\", "/")

            matches.append(GrepMatch(
                file_path=file_path,
                line_no=int(m.group(2)),
                line_content=m.group(3),
                context_lines=list(current_context),
            ))
            current_context = []
        else:
            # 上下文行
            ctx_m = re.match(r'^(.+?)-(\d+)-(.*)$', line)
            if ctx_m:
                current_context.append(ctx_m.group(3))

    return matches


def _grep_with_python(
    repo_dir: str,
    patterns: List[str],
    context_lines: int = 3,
    max_results_per_pattern: int = 10,
) -> List[GrepMatch]:
    """Python 原生文本搜索（Windows 兼容 fallback）"""
    results = []

    for pattern in patterns:
        count = 0
        for root, dirs, files in os.walk(repo_dir):
            # 就地过滤跳过目录（修改 dirs 列表）
            dirs[:] = [d for d in dirs if not _should_skip_dir(d)]

            for filename in files:
                if _is_binary_file(filename):
                    continue

                file_full_path = os.path.join(root, filename)
                file_rel_path = os.path.relpath(file_full_path, repo_dir).replace("\\", "/")

                try:
                    with open(file_full_path, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                except (IOError, OSError):
                    continue

                for line_idx, line_text in enumerate(lines):
                    if pattern in line_text:
                        line_no = line_idx + 1  # 1-indexed

                        # 收集上下文行
                        ctx_start = max(0, line_idx - context_lines)
                        ctx_end = min(len(lines), line_idx + context_lines + 1)
                        ctx = [
                            lines[i].rstrip("\n\r")
                            for i in range(ctx_start, ctx_end)
                            if i != line_idx
                        ]

                        results.append(GrepMatch(
                            file_path=file_rel_path,
                            line_no=line_no,
                            line_content=line_text.rstrip("\n\r"),
                            context_lines=ctx,
                        ))

                        count += 1
                        if count >= max_results_per_pattern:
                            break

                if count >= max_results_per_pattern:
                    break

            if count >= max_results_per_pattern:
                break

    return results


async def grep_codebase(
    repo_id: str,
    patterns: List[str],
    context_lines: int = 3,
    repo_dir: Optional[str] = None,
) -> List[GrepMatch]:
    """
    对克隆到本地的代码仓库做文本精确搜索。

    参数:
        repo_id: 仓库 ID
        patterns: 搜索模式列表（从 query 中提取的标识符、字面量等）
        context_lines: 每个匹配结果的上下文行数
        repo_dir: 可选，仓库根目录绝对路径。
                  应由调用方从 Repository.local_path 获取后传入。
                  若为空，回退到 settings.REPOS_BASE_DIR/repo_id。

    返回:
        GrepMatch 列表，包含文件路径、行号、行内容和上下文

    实现策略:
        优先使用系统 rg (ripgrep)，若不可用则自动降级为 Python 原生搜索。
        两条路径的输出格式完全一致。
        内部阻塞操作通过 asyncio.to_thread 调度，不阻塞事件循环。
    """
    if not patterns:
        return []

    if not repo_dir:
        repo_dir = os.path.join(settings.REPOS_BASE_DIR, repo_id)
    if not os.path.isdir(repo_dir):
        logger.warning(f"[Grep] 仓库目录不存在: {repo_dir}")
        return []

    import asyncio

    if _check_rg_available():
        logger.debug("[Grep] 使用 ripgrep (rg)")
        return await asyncio.to_thread(_grep_with_rg, repo_dir, patterns, context_lines)
    else:
        logger.debug("[Grep] ripgrep 不可用，使用 Python 原生搜索")
        return await asyncio.to_thread(_grep_with_python, repo_dir, patterns, context_lines)
```

### 3.3 关键实现细节

#### `extract_grep_patterns` 的设计考虑

- **不使用正则搜索**：`_grep_with_python` 使用 `pattern in line_text`（`str.__contains__`），因为从 query 提取的模式是字面量，不是正则。如果未来需要正则支持，可在此处扩展
- **引号字面量优先**：用户用引号明确标识的字符串最可能是精确搜索目标
- **最多 10 个模式**：防止 query 过长时产生过多搜索模式，影响性能

#### 性能约束

- `_grep_with_rg`：每个 pattern 设置 `--max-count 10`，timeout 30s
- `_grep_with_python`：每个 pattern 最多 10 个结果，遇到即停
- `_check_rg_available` 会在每次调用时检测。如果性能敏感，可以缓存结果（模块级变量）

#### 非阻塞调度

`_grep_with_rg`（subprocess.run）和 `_grep_with_python`（os.walk + 文件读取）都是阻塞操作。在 async chat 流程中直接调用会卡住事件循环，影响 SSE 推送和并发请求。

**必须**在 `grep_codebase()` 内部使用 `asyncio.to_thread()` 包装这两个函数。`search_file_paths()` 是纯内存操作，无需 `to_thread`。

#### 仓库路径来源

现有系统中 `Repository.local_path`（`app/models/repository.py` 第 36 行）是仓库实际磁盘路径的权威字段。`settings.REPOS_BASE_DIR/repo_id` 只是默认约定，在迁移目录或历史数据场景下可能不一致。

`grep_codebase()` 接受可选 `repo_dir` 参数，由调用方（Plan 04 的 `_three_way_retrieval`）从 `Repository.local_path` 获取后传入。若未传入则回退到默认路径，与现有 `read_file_context` 行为一致。

---

## 4. 测试要求

### 4.1 `tests/unit/test_code_searcher.py`（新建）

```python
"""
Unit tests for app/services/code_searcher.py
验证路径搜索、grep 模式提取、Python fallback 搜索的正确性。
"""
import os
import pytest
import tempfile

from app.services.code_searcher import (
    _tokenize_path,
    _tokenize_query,
    search_file_paths,
    extract_grep_patterns,
    _grep_with_python,
    _is_binary_file,
    _should_skip_dir,
)


class TestTokenizePath:
    def test_simple_path(self):
        tokens = _tokenize_path("app/services/chat_service.py")
        assert "app" in tokens
        assert "services" in tokens
        assert "chat" in tokens
        assert "service" in tokens
        assert "py" in tokens

    def test_camel_case(self):
        tokens = _tokenize_path("src/ChatService.ts")
        assert "chat" in tokens
        assert "service" in tokens

    def test_windows_path(self):
        tokens = _tokenize_path("app\\services\\chat_service.py")
        assert "app" in tokens
        assert "chat" in tokens


class TestTokenizeQuery:
    def test_camel_case_extraction(self):
        tokens = _tokenize_query("ChatService 在哪里定义")
        assert "chat" in tokens
        assert "service" in tokens

    def test_snake_case_extraction(self):
        tokens = _tokenize_query("handle_chat_stream 怎么工作")
        assert "handle" in tokens
        assert "chat" in tokens
        assert "stream" in tokens

    def test_stop_words_filtered(self):
        tokens = _tokenize_query("the function for this task")
        assert "the" not in tokens
        assert "for" not in tokens
        assert "this" not in tokens


class TestSearchFilePaths:
    @pytest.mark.asyncio
    async def test_basic_match(self):
        index_data = {
            "app/services/chat_service.py": {"functions": ["handle_chat"]},
            "app/services/parser.py": {"functions": ["parse_repo"]},
            "app/config.py": {"constants": ["DEBUG"]},
        }
        result = await search_file_paths("repo1", "chat service", index_data)
        assert "app/services/chat_service.py" in result

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self):
        index_data = {"app/main.py": {}}
        result = await search_file_paths("repo1", "", index_data)
        assert result == []

    @pytest.mark.asyncio
    async def test_no_index_returns_empty(self):
        result = await search_file_paths("repo1", "something", None)
        assert result == []

    @pytest.mark.asyncio
    async def test_max_10_results(self):
        index_data = {f"file_{i}.py": {} for i in range(20)}
        result = await search_file_paths("repo1", "file", index_data)
        assert len(result) <= 10


class TestExtractGrepPatterns:
    def test_quoted_literal(self):
        patterns = extract_grep_patterns('找 "MultipleResultsFound" 在哪')
        assert "MultipleResultsFound" in patterns

    def test_camel_case_identifier(self):
        patterns = extract_grep_patterns("ChatService 类的定义")
        assert "ChatService" in patterns

    def test_snake_case_identifier(self):
        patterns = extract_grep_patterns("handle_chat_stream 函数")
        assert "handle_chat_stream" in patterns

    def test_route_path(self):
        patterns = extract_grep_patterns("路由 /api/repositories 在哪个文件")
        assert "/api/repositories" in patterns

    def test_all_caps_constant(self):
        patterns = extract_grep_patterns("BUDGET_RATIO 常量")
        assert "BUDGET_RATIO" in patterns

    def test_max_10_patterns(self):
        long_query = " ".join([f"func_{i}" for i in range(20)])
        patterns = extract_grep_patterns(long_query)
        assert len(patterns) <= 10


class TestGrepWithPython:
    def test_basic_search_in_temp_dir(self):
        """在临时目录中创建文件并搜索。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建测试文件
            test_file = os.path.join(tmpdir, "test.py")
            with open(test_file, "w", encoding="utf-8") as f:
                f.write("line 1\nBUDGET_RATIO = 0.8\nline 3\n")

            results = _grep_with_python(tmpdir, ["BUDGET_RATIO"], context_lines=1)
            assert len(results) >= 1
            assert results[0].line_no == 2
            assert "BUDGET_RATIO" in results[0].line_content

    def test_skip_binary_files(self):
        """二进制文件不应被搜索。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_file = os.path.join(tmpdir, "image.png")
            with open(bin_file, "wb") as f:
                f.write(b"fake png content with BUDGET_RATIO")

            results = _grep_with_python(tmpdir, ["BUDGET_RATIO"])
            assert len(results) == 0

    def test_skip_excluded_dirs(self):
        """排除目录（.git, node_modules 等）不应被搜索。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = os.path.join(tmpdir, ".git")
            os.makedirs(git_dir)
            git_file = os.path.join(git_dir, "config")
            with open(git_file, "w") as f:
                f.write("SEARCH_TARGET = true\n")

            results = _grep_with_python(tmpdir, ["SEARCH_TARGET"])
            assert len(results) == 0

    def test_context_lines_included(self):
        """搜索结果应包含上下文行。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = os.path.join(tmpdir, "code.py")
            with open(test_file, "w") as f:
                f.write("line1\nline2\nTARGET_LINE\nline4\nline5\n")

            results = _grep_with_python(tmpdir, ["TARGET_LINE"], context_lines=1)
            assert len(results) >= 1
            assert len(results[0].context_lines) > 0

    def test_relative_path_with_forward_slashes(self):
        """返回的 file_path 使用正斜杠（跨平台一致性）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            sub_dir = os.path.join(tmpdir, "src", "services")
            os.makedirs(sub_dir)
            test_file = os.path.join(sub_dir, "main.py")
            with open(test_file, "w") as f:
                f.write("FIND_ME = True\n")

            results = _grep_with_python(tmpdir, ["FIND_ME"])
            assert len(results) >= 1
            assert "\\" not in results[0].file_path  # 无反斜杠
            assert "src/services/main.py" == results[0].file_path


class TestFilterHelpers:
    def test_binary_extensions_detected(self):
        assert _is_binary_file("image.png") is True
        assert _is_binary_file("data.sqlite3") is True
        assert _is_binary_file("script.py") is False
        assert _is_binary_file("config.json") is False

    def test_excluded_dirs_detected(self):
        assert _should_skip_dir(".git") is True
        assert _should_skip_dir("node_modules") is True
        assert _should_skip_dir("__pycache__") is True
        assert _should_skip_dir("app") is False
        assert _should_skip_dir("src") is False
```

### 3.4 双路径语义等价契约

`_grep_with_rg()` 和 `_grep_with_python()` 作为 `grep_codebase()` 的两条等价执行路径，**必须满足以下语义等价约束**。任一约束不满足即视为 bug。

| 维度 | rg 路径 | Python 路径 | 等价要求 |
|---|---|---|---|
| **目录过滤** | `--glob !{dir}/` 排除 `_EXCLUDED_DIRS` | `dirs[:] = [d for d in dirs if not _should_skip_dir(d)]` | 同一 `_EXCLUDED_DIRS` 集合，子目录递归均跳过 |
| **文件过滤** | `--type-not binary` (rg 内置) | `_is_binary_file()` 基于 `_BINARY_EXTENSIONS` | Python 路径的 `_BINARY_EXTENSIONS` 覆盖 rg 内置二进制判断的常见类型；允许 rg 过滤更多（超集），但 Python 不可漏过 `_BINARY_EXTENSIONS` 内的类型 |
| **匹配语义** | rg 默认 literal pattern（无 `-e` 时为 fixed string 模式） | `pattern in line_text`（Python `str.__contains__`） | 均为子串匹配（非正则）。rg 需显式传入 `--fixed-strings`（`-F`）确保与 Python 一致 |
| **结果字段** | `GrepMatch(file_path, line_no, line_content, context_lines)` | 同左 | 字段语义完全相同 |
| **file_path 格式** | `os.path.relpath(abs, repo_dir).replace("\\", "/")` | 同左 | 始终为相对于 `repo_dir` 的正斜杠路径 |
| **line_no** | 1-indexed（rg 默认行为） | `line_idx + 1`（1-indexed） | 一致 |
| **context_lines** | rg `--context N` 输出的上下文行 | `lines[ctx_start:ctx_end]` 排除匹配行本身 | 均为匹配行前后各 N 行，不含匹配行本身 |
| **结果排序** | 文件发现顺序（rg 默认目录遍历序） | `os.walk()` 目录遍历序 | **不要求顺序一致**——调用方 `merge_retrieval_results()` 按 `(file_path, chunk_id)` 去重，不依赖结果顺序 |
| **每 pattern 上限** | `--max-count 10` | `count >= max_results_per_pattern: break` | 均默认 10，由参数 `max_results_per_pattern` 统一控制 |
| **编码处理** | `--encoding utf-8`（rg 默认），`errors="replace"` | `open(..., encoding="utf-8", errors="replace")` | 均使用 UTF-8 + replacement 策略 |

**rg 路径需补充的修正**：

当前 `_grep_with_rg()` 未传 `--fixed-strings`（`-F`），依赖 rg 的智能模式检测（smart case + auto regex）。因为 `extract_grep_patterns()` 提取的模式可能包含正则元字符（如括号、点号），**必须添加 `-F` 参数**确保与 Python 路径的 `in` 匹配语义一致：

```python
cmd = [
    "rg",
    "--no-heading",
    "--line-number",
    "--fixed-strings",  # ← 新增：确保 literal 匹配，与 Python `in` 语义一致
    "--max-count", str(max_results_per_pattern),
    "--context", str(context_lines),
    "--type-not", "binary",
]
```

**验证等价性的测试**（在 `test_code_searcher.py` 中新增）：

```python
class TestDualPathEquivalence:
    """验证 rg 路径和 Python 路径在相同输入下产出语义等价的结果。"""

    def test_python_and_rg_same_matches(self):
        """对同一目录同一 pattern，两条路径的匹配行号集合应一致。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = os.path.join(tmpdir, "sample.py")
            with open(test_file, "w", encoding="utf-8") as f:
                f.write("alpha\nBUDGET_RATIO = 0.8\ngamma\nBUDGET_RATIO_MAX = 1.0\n")

            py_results = _grep_with_python(tmpdir, ["BUDGET_RATIO"], context_lines=1)
            py_hits = {(r.file_path, r.line_no) for r in py_results}

            # 仅当 rg 可用时才对比
            if _check_rg_available():
                from app.services.code_searcher import _grep_with_rg
                rg_results = _grep_with_rg(tmpdir, ["BUDGET_RATIO"], context_lines=1)
                rg_hits = {(r.file_path, r.line_no) for r in rg_results}
                assert py_hits == rg_hits, f"Python hits {py_hits} != rg hits {rg_hits}"

    def test_excluded_dirs_both_paths(self):
        """两条路径都跳过 _EXCLUDED_DIRS 中的目录。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 在 node_modules 下放匹配文件
            nm_dir = os.path.join(tmpdir, "node_modules")
            os.makedirs(nm_dir)
            with open(os.path.join(nm_dir, "lib.js"), "w") as f:
                f.write("TARGET_SYMBOL = true\n")
            # 在正常目录下放匹配文件
            with open(os.path.join(tmpdir, "main.py"), "w") as f:
                f.write("TARGET_SYMBOL = true\n")

            py_results = _grep_with_python(tmpdir, ["TARGET_SYMBOL"])
            assert all("node_modules" not in r.file_path for r in py_results)
            assert len(py_results) == 1

            if _check_rg_available():
                from app.services.code_searcher import _grep_with_rg
                rg_results = _grep_with_rg(tmpdir, ["TARGET_SYMBOL"])
                assert all("node_modules" not in r.file_path for r in rg_results)
                assert len(rg_results) == 1
```

---

## 5. 验收标准

1. **`search_file_paths()` 可调用**：传入 `index_data` 和 query 后返回匹配文件路径列表
2. **`grep_codebase()` 双路径工作**：rg 可用时使用 rg，不可用时自动降级为 Python 搜索
3. **`extract_grep_patterns()` 正确提取**：引号字面量、CamelCase、snake_case、ALL_CAPS、路由路径
4. **Windows 兼容**：Python fallback 在 Windows 上正确工作，路径使用正斜杠
5. **排除规则生效**：`.git`、`node_modules`、二进制文件被正确跳过
6. **所有测试通过**：`pytest tests/unit/test_code_searcher.py -v`
7. **无生产代码依赖变更**：仅新增文件和在 `mcp_types.py` 添加 `GrepMatch`

---

## 6. 风险与注意事项

- **`_check_rg_available()` 性能**：每次 `grep_codebase()` 调用都检测 rg。可考虑模块级缓存变量 `_RG_AVAILABLE: Optional[bool] = None`
- **大型仓库搜索性能**：Python fallback 在大型仓库上可能较慢。`max_results_per_pattern=10` 和早停机制可缓解，但极端情况（10万文件+）可能需要额外的文件数上限
- **编码兼容**：`open(..., errors="replace")` 确保 UTF-8 无法解码的文件不会 crash，但内容可能含 replacement character
- **`extract_grep_patterns` 的假阳性**：某些短词可能被错误提取为 grep 模式（如变量名 `id`），但 `len(p) >= 2` 的过滤可以缓解大部分情况
