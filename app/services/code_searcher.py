"""
code_searcher.py - 路径搜索 + Grep 搜索。

提供两个独立搜索函数：
1. search_file_paths: 基于 RepoIndex 的文件路径 token 级匹配
2. grep_codebase: 基于本地克隆仓库的文本精确搜索
"""
import asyncio
import logging
import os
import re
import subprocess
from typing import Dict, List, Optional

from app.config import settings
from app.schemas.mcp_types import GrepMatch

logger = logging.getLogger(__name__)

_STOP_WORDS = {
    "the", "and", "for", "with", "that", "this", "from", "have", "will",
    "what", "how", "can", "are", "was", "but", "not", "all", "been",
    "has", "its", "into", "than", "then", "who", "did", "get", "may",
    "new", "one", "our", "out", "say", "where", "which",
    "在", "的", "了", "是", "有", "和", "与", "这", "那", "吗", "呢",
    "哪", "什么", "怎么", "如何", "为什么", "哪里", "哪个",
}

_EXCLUDED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".tox", ".eggs", "dist", "build", ".mypy_cache", ".pytest_cache",
}

_BINARY_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".bin", ".jar", ".class",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2",
    ".ttf", ".eot", ".pdf", ".zip", ".tar", ".gz", ".bz2", ".7z",
    ".mp3", ".mp4", ".avi", ".mov", ".db", ".sqlite", ".sqlite3",
}

_PATH_PART_PATTERN = re.compile(r"[/\\.]")
_IDENTIFIER_PATTERN = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|[0-9]+")
_QUERY_TOKEN_PATTERN = re.compile(
    r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|[a-z][a-z0-9_]*(?:_[a-z0-9]+)+|"
    r"[A-Z][A-Z0-9_]+|[\u4e00-\u9fff]+"
)
_RG_MATCH_PATTERN = re.compile(r"^(.+?):(\d+):(.*)$")
_RG_CONTEXT_PATTERN = re.compile(r"^(.+?)-(\d+)-(.*)$")
_RG_AVAILABLE: Optional[bool] = None


def _tokenize_path(path: str) -> List[str]:
    """将文件路径拆分为可搜索的 token 列表。"""
    tokens: List[str] = []
    for part in _PATH_PART_PATTERN.split(path):
        if not part:
            continue

        if "_" in part:
            for sub_part in part.split("_"):
                if not sub_part:
                    continue
                tokens.extend(token.lower() for token in _IDENTIFIER_PATTERN.findall(sub_part) or [sub_part])
            continue

        tokens.extend(token.lower() for token in _IDENTIFIER_PATTERN.findall(part) or [part])

    return tokens


def _tokenize_query(query: str) -> List[str]:
    """将用户查询拆分为搜索 token。"""
    tokens: List[str] = []
    for raw_token in _QUERY_TOKEN_PATTERN.findall(query):
        if "_" in raw_token and not raw_token.isupper():
            candidates = [part for part in raw_token.split("_") if part]
        else:
            candidates = _IDENTIFIER_PATTERN.findall(raw_token) or [raw_token]

        for candidate in candidates:
            token = candidate.lower()
            if len(token) < 2 or token in _STOP_WORDS:
                continue
            tokens.append(token)

    return tokens


async def search_file_paths(
    repo_id: str,
    query: str,
    index_data: Optional[Dict[str, object]] = None,
) -> List[str]:
    """
    在 RepoIndex 的文件路径中做 token 级匹配。

    repo_id 当前仅用于保持接口与后续调用方一致。
    """
    _ = repo_id

    if index_data is None:
        logger.warning("[PathSearch] index_data 为空，跳过路径搜索")
        return []

    query_tokens = _tokenize_query(query)
    if not query_tokens:
        return []

    scored_paths = []
    for file_path in index_data.keys():
        path_tokens = _tokenize_path(file_path)
        if not path_tokens:
            continue

        path_token_set = set(path_tokens)
        match_count = sum(1 for token in query_tokens if token in path_token_set)
        if match_count <= 0:
            continue

        score = match_count / len(query_tokens)
        scored_paths.append((file_path, score))

    scored_paths.sort(key=lambda item: (-item[1], item[0]))
    return [file_path for file_path, _ in scored_paths[:10]]


def _is_binary_file(file_path: str) -> bool:
    """根据扩展名判断是否为二进制文件。"""
    _, ext = os.path.splitext(file_path)
    return ext.lower() in _BINARY_EXTENSIONS


def _should_skip_dir(dir_name: str) -> bool:
    """判断目录是否应跳过。"""
    return dir_name in _EXCLUDED_DIRS


def _check_rg_available() -> bool:
    """检测系统是否安装了 ripgrep (rg)。"""
    global _RG_AVAILABLE

    if _RG_AVAILABLE is not None:
        return _RG_AVAILABLE

    try:
        result = subprocess.run(
            ["rg", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
        )
        _RG_AVAILABLE = result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        _RG_AVAILABLE = False

    return _RG_AVAILABLE


def extract_grep_patterns(query: str) -> List[str]:
    """从用户查询中提取 grep 搜索模式。"""
    patterns: List[str] = []
    patterns.extend(re.findall(r'["\']([^"\']{2,})["\']', query))
    patterns.extend(re.findall(r"\b([A-Z][a-z]+(?:[A-Z][a-z0-9]+)+)\b", query))
    patterns.extend(re.findall(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b", query))
    patterns.extend(re.findall(r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\b", query))
    patterns.extend(re.findall(r"(/[a-zA-Z0-9_/{}]+)", query))

    unique_patterns: List[str] = []
    seen = set()
    for pattern in patterns:
        if len(pattern) < 2 or pattern in seen:
            continue
        seen.add(pattern)
        unique_patterns.append(pattern)

    return unique_patterns[:10]


def _normalize_rel_path(file_path: str, repo_dir: str) -> str:
    """将路径规范化为相对 repo_dir 的正斜杠路径。"""
    normalized_repo_dir = os.path.normcase(os.path.abspath(repo_dir))
    normalized_file_path = os.path.normcase(os.path.abspath(file_path))
    if normalized_file_path.startswith(normalized_repo_dir):
        rel_path = os.path.relpath(file_path, repo_dir)
    else:
        rel_path = file_path
    return rel_path.replace("\\", "/")


def _parse_rg_output(output: str, repo_dir: str, context_lines: int = 3) -> List[GrepMatch]:
    """解析 rg 输出并转为 GrepMatch 列表。"""
    matches: List[GrepMatch] = []
    current_block: List[Dict[str, object]] = []

    def flush_block() -> None:
        if not current_block:
            return

        for entry in current_block:
            if not entry["is_match"]:
                continue

            file_path = entry["file_path"]
            line_no = entry["line_no"]
            context = [
                block_entry["content"]
                for block_entry in current_block
                if (
                    not block_entry["is_match"]
                    and block_entry["file_path"] == file_path
                    and abs(block_entry["line_no"] - line_no) <= context_lines
                )
            ]

            matches.append(
                GrepMatch(
                    file_path=_normalize_rel_path(file_path, repo_dir),
                    line_no=line_no,
                    line_content=entry["content"],
                    context_lines=context,
                )
            )

        current_block.clear()

    for raw_line in output.splitlines():
        if raw_line == "--":
            flush_block()
            continue

        if not raw_line.strip():
            continue

        match_line = _RG_MATCH_PATTERN.match(raw_line)
        if match_line:
            file_path, line_no, line_content = match_line.groups()
            current_block.append(
                {
                    "is_match": True,
                    "file_path": file_path,
                    "line_no": int(line_no),
                    "content": line_content,
                }
            )
            continue

        context_line = _RG_CONTEXT_PATTERN.match(raw_line)
        if context_line:
            file_path, line_no, line_content = context_line.groups()
            current_block.append(
                {
                    "is_match": False,
                    "file_path": file_path,
                    "line_no": int(line_no),
                    "content": line_content,
                }
            )

    flush_block()

    return matches


def _grep_with_rg(
    repo_dir: str,
    patterns: List[str],
    context_lines: int = 3,
    max_results_per_pattern: int = 10,
) -> List[GrepMatch]:
    """使用 ripgrep 执行固定字符串搜索。"""
    results: List[GrepMatch] = []

    for pattern in patterns:
        cmd = [
            "rg",
            "--no-heading",
            "--line-number",
            "--fixed-strings",
            "--context",
            str(context_lines),
            "--max-count",
            str(max_results_per_pattern),
        ]
        for excluded_dir in sorted(_EXCLUDED_DIRS):
            cmd.extend(["--glob", f"!{excluded_dir}/**"])
        cmd.extend([pattern, repo_dir])

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="replace",
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.debug("[Grep/rg] pattern '%s' failed: %s", pattern, exc)
            continue

        if proc.returncode not in (0, 1):
            logger.debug("[Grep/rg] pattern '%s' exited with code %s", pattern, proc.returncode)
            continue
        if not proc.stdout:
            continue

        results.extend(_parse_rg_output(proc.stdout, repo_dir, context_lines))

    return results


def _grep_with_python(
    repo_dir: str,
    patterns: List[str],
    context_lines: int = 3,
    max_results_per_pattern: int = 10,
) -> List[GrepMatch]:
    """Python 原生文本搜索（Windows 兼容 fallback）。"""
    results: List[GrepMatch] = []

    for pattern in patterns:
        pattern_result_count = 0
        for root, dirs, files in os.walk(repo_dir):
            dirs[:] = [dir_name for dir_name in dirs if not _should_skip_dir(dir_name)]

            for filename in files:
                if _is_binary_file(filename):
                    continue

                full_path = os.path.join(root, filename)
                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as file_obj:
                        lines = file_obj.readlines()
                except (OSError, UnicodeError):
                    continue

                for line_index, line_text in enumerate(lines):
                    if pattern not in line_text:
                        continue

                    context_start = max(0, line_index - context_lines)
                    context_end = min(len(lines), line_index + context_lines + 1)
                    context = [
                        lines[index].rstrip("\r\n")
                        for index in range(context_start, context_end)
                        if index != line_index
                    ]

                    results.append(
                        GrepMatch(
                            file_path=_normalize_rel_path(full_path, repo_dir),
                            line_no=line_index + 1,
                            line_content=line_text.rstrip("\r\n"),
                            context_lines=context,
                        )
                    )

                    pattern_result_count += 1
                    if pattern_result_count >= max_results_per_pattern:
                        break

                if pattern_result_count >= max_results_per_pattern:
                    break

            if pattern_result_count >= max_results_per_pattern:
                break

    return results


async def grep_codebase(
    repo_id: str,
    patterns: List[str],
    context_lines: int = 3,
    repo_dir: Optional[str] = None,
) -> List[GrepMatch]:
    """对克隆到本地的代码仓库做文本精确搜索。"""
    if not patterns:
        return []

    effective_repo_dir = repo_dir or os.path.join(settings.REPOS_BASE_DIR, repo_id)
    if not os.path.isdir(effective_repo_dir):
        logger.warning("[Grep] 仓库目录不存在: %s", effective_repo_dir)
        return []

    if _check_rg_available():
        logger.debug("[Grep] 使用 ripgrep (rg)")
        return await asyncio.to_thread(_grep_with_rg, effective_repo_dir, patterns, context_lines)

    logger.debug("[Grep] ripgrep 不可用，使用 Python 原生搜索")
    return await asyncio.to_thread(_grep_with_python, effective_repo_dir, patterns, context_lines)
