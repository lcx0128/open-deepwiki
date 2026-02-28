"""
Open-DeepWiki MCP Server

暴露代码库知识图谱给 AI 编程工具（Claude Code、Gemini CLI 等）。

使用方式:
  stdio 模式（本地 Claude Code）:
    python -m app.mcp_server --transport stdio

  HTTP 模式（远程 Agent）:
    python -m app.mcp_server --transport http --port 8808

Claude Code 配置示例:
  claude mcp add deepwiki -- python -m app.mcp_server --transport stdio
"""

import logging
import os
import re
import sys
from typing import Optional

# MCP
from mcp.server.fastmcp import FastMCP

# App
from app.config import settings
from app.database import async_session_factory
from app.models.repository import Repository, RepoStatus
from app.models.repo_index import RepoIndex  # noqa: F401 — ensures mapper resolves relationship
from app.models.wiki import Wiki, WikiSection, WikiPage
from app.schemas.mcp_types import CodeGuideline, FileContext
from app.services.two_stage_retriever import stage1_discovery, stage2_assembly, read_file_context
from app.services.embedder import get_collection

# SQLAlchemy
from sqlalchemy import select
from sqlalchemy.orm import selectinload

# Logging — 必须使用 stderr，确保 stdio 模式下不污染 stdout
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

mcp = FastMCP("open-deepwiki")


# ---------------------------------------------------------------------------
# Tool 1: list_repositories
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_repositories() -> list[dict]:
    """
    列出所有已就绪（status=ready）的代码库。

    返回每个仓库的基本信息，包括 repo_id、名称、URL、状态和最后同步时间。
    使用 repo_id 作为其他工具的参数。
    """
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(Repository)
                .where(Repository.status == RepoStatus.READY)
                .order_by(Repository.created_at.desc())
            )
            repos = result.scalars().all()

        return [
            {
                "repo_id": repo.id,
                "name": repo.name,
                "url": repo.url,
                "status": repo.status.value if repo.status else "unknown",
                "last_synced_at": repo.last_synced_at.isoformat() if repo.last_synced_at else None,
            }
            for repo in repos
        ]
    except Exception as e:
        logger.error(f"[list_repositories] 查询失败: {e}")
        return [{"error": str(e), "tool": "list_repositories"}]


# ---------------------------------------------------------------------------
# Tool 2: search_codebase
# ---------------------------------------------------------------------------

@mcp.tool()
async def search_codebase(query: str, repo_id: str, top_k: int = 10) -> list[dict]:
    """
    语义搜索代码库，返回相关代码片段的轻量导引（不含完整代码）。使用 get_code_chunks 获取完整代码内容。

    参数:
      query:   自然语言搜索词，例如 "用户认证逻辑" 或 "数据库连接池初始化"
      repo_id: 仓库 ID（来自 list_repositories）
      top_k:   返回结果数量上限（默认 10，最大 50）

    返回每个匹配 chunk 的元数据：chunk_id、名称、文件路径、节点类型、行号范围、描述和相关度分数。
    chunk_id 可传入 get_code_chunks 获取完整代码。
    """
    try:
        top_k = min(max(1, top_k), 50)
        guidelines: list[CodeGuideline] = await stage1_discovery(query, repo_id, top_k)
        return [g.model_dump() for g in guidelines]
    except Exception as e:
        logger.error(f"[search_codebase] repo={repo_id}, query='{query[:50]}', 错误: {e}")
        return [{"error": str(e), "tool": "search_codebase"}]


# ---------------------------------------------------------------------------
# Tool 3: get_code_chunks
# ---------------------------------------------------------------------------

# 解析 stage2_assembly 返回字符串的正则：
# "// File: {path} (Lines {start}-{end})\n{code}"
_CHUNK_HEADER_RE = re.compile(
    r"^// File: (?P<file_path>.+?) \(Lines (?P<start>\d+)-(?P<end>\d+)\)\n",
    re.MULTILINE,
)


@mcp.tool()
async def get_code_chunks(repo_id: str, chunk_ids: list[str]) -> list[dict]:
    """
    获取指定 chunk 的完整代码内容。

    先用 search_codebase 获取 chunk_id，再用此工具取回完整代码。

    参数:
      repo_id:   仓库 ID
      chunk_ids: chunk ID 列表（来自 search_codebase 的结果）

    返回每个 chunk 的结构化内容：chunk_id、文件路径、起止行号和代码正文。
    """
    try:
        if not chunk_ids:
            return []

        raw_list: list[str] = await stage2_assembly(chunk_ids, repo_id)

        results = []
        for i, raw in enumerate(raw_list):
            chunk_id = chunk_ids[i] if i < len(chunk_ids) else f"chunk_{i}"
            m = _CHUNK_HEADER_RE.match(raw)
            if m:
                file_path = m.group("file_path")
                start_line = int(m.group("start"))
                end_line = int(m.group("end"))
                content = raw[m.end():]
            else:
                file_path = ""
                start_line = 0
                end_line = 0
                content = raw

            results.append({
                "chunk_id": chunk_id,
                "file_path": file_path,
                "start_line": start_line,
                "end_line": end_line,
                "content": content,
            })

        return results
    except Exception as e:
        logger.error(f"[get_code_chunks] repo={repo_id}, 错误: {e}")
        return [{"error": str(e), "tool": "get_code_chunks"}]


# ---------------------------------------------------------------------------
# Tool 4: read_file
# ---------------------------------------------------------------------------

@mcp.tool()
async def read_file(
    repo_id: str,
    file_path: str,
    start_line: int = 1,
    end_line: int = 0,
) -> dict:
    """
    读取仓库中指定文件的内容（支持行范围）。

    参数:
      repo_id:    仓库 ID
      file_path:  文件的相对路径，例如 "app/main.py" 或 "src/utils/helper.ts"
      start_line: 起始行号（1-indexed，默认 1）
      end_line:   结束行号（1-indexed，默认 0 表示读到文件末尾）

    返回文件内容、语言类型、行范围和总行数。
    """
    try:
        # 安全检查：拒绝路径穿越和绝对路径
        if ".." in file_path or os.path.isabs(file_path):
            return {"error": f"非法文件路径: {file_path}", "tool": "read_file"}

        # 构造并验证真实路径
        repo_base = os.path.realpath(os.path.join(settings.REPOS_BASE_DIR, repo_id))
        full_path = os.path.realpath(os.path.join(settings.REPOS_BASE_DIR, repo_id, file_path))

        if not full_path.startswith(repo_base + os.sep) and full_path != repo_base:
            return {"error": f"路径穿越检测: {file_path}", "tool": "read_file"}

        if not os.path.exists(full_path):
            return {"error": f"文件不存在: {file_path}", "tool": "read_file"}

        if not os.path.isfile(full_path):
            return {"error": f"路径不是文件: {file_path}", "tool": "read_file"}

        # 先统计总行数，处理 end_line=0 的情况
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            total_lines = sum(1 for _ in f)

        actual_end = total_lines if end_line == 0 else min(end_line, total_lines)
        actual_start = max(1, start_line)

        if actual_start > total_lines:
            return {
                "error": f"start_line={actual_start} 超过文件总行数={total_lines}",
                "tool": "read_file",
            }

        ctx: FileContext = read_file_context(repo_id, file_path, actual_start, actual_end)

        return {
            "file_path": ctx.file_path,
            "content": ctx.content,
            "language": ctx.language,
            "start_line": ctx.start_line,
            "end_line": ctx.end_line,
            "total_lines": total_lines,
        }
    except FileNotFoundError as e:
        logger.warning(f"[read_file] 文件不存在: {file_path}")
        return {"error": f"文件不存在: {file_path}", "tool": "read_file"}
    except Exception as e:
        logger.error(f"[read_file] repo={repo_id}, path={file_path}, 错误: {e}")
        return {"error": str(e), "tool": "read_file"}


# ---------------------------------------------------------------------------
# Tool 5: get_repository_overview
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_repository_overview(repo_id: str) -> dict:
    """
    获取仓库的整体概览，包括基本信息和 Wiki 目录结构。

    参数:
      repo_id: 仓库 ID（来自 list_repositories）

    返回仓库元数据、Wiki 标题、所有章节和页面的摘要（每页前 200 字符）。
    使用 get_wiki_content 获取完整页面内容。
    """
    try:
        async with async_session_factory() as db:
            # 查询仓库
            repo_result = await db.execute(
                select(Repository).where(Repository.id == repo_id)
            )
            repo: Optional[Repository] = repo_result.scalar_one_or_none()

            if repo is None:
                return {"error": f"仓库不存在: {repo_id}", "tool": "get_repository_overview"}

            if repo.status != RepoStatus.READY:
                return {
                    "error": f"仓库尚未就绪，当前状态: {repo.status.value}",
                    "tool": "get_repository_overview",
                }

            # 查询最新 Wiki（eager load sections -> pages）
            wiki_result = await db.execute(
                select(Wiki)
                .where(Wiki.repo_id == repo_id)
                .options(
                    selectinload(Wiki.sections).selectinload(WikiSection.pages)
                )
                .order_by(Wiki.created_at.desc())
                .limit(1)
            )
            wiki: Optional[Wiki] = wiki_result.scalar_one_or_none()

        overview: dict = {
            "repo_id": repo.id,
            "repo_name": repo.name,
            "repo_url": repo.url,
            "status": repo.status.value,
            "last_synced_at": repo.last_synced_at.isoformat() if repo.last_synced_at else None,
        }

        if wiki is None:
            overview["wiki_title"] = None
            overview["sections"] = []
            overview["total_sections"] = 0
            overview["total_pages"] = 0
            return overview

        sections_data = []
        total_pages = 0
        for section in wiki.sections:
            pages_data = []
            for page in section.pages:
                summary = ""
                if page.content_md:
                    summary = page.content_md[:200]
                pages_data.append({
                    "title": page.title,
                    "importance": page.importance,
                    "summary": summary,
                })
                total_pages += 1
            sections_data.append({
                "title": section.title,
                "pages": pages_data,
            })

        overview["wiki_title"] = wiki.title
        overview["sections"] = sections_data
        overview["total_sections"] = len(sections_data)
        overview["total_pages"] = total_pages

        return overview

    except Exception as e:
        logger.error(f"[get_repository_overview] repo={repo_id}, 错误: {e}")
        return {"error": str(e), "tool": "get_repository_overview"}


# ---------------------------------------------------------------------------
# Tool 6: get_wiki_content
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_wiki_content(repo_id: str, section_title: str = "") -> list[dict]:
    """
    获取仓库 Wiki 的完整内容。

    参数:
      repo_id:       仓库 ID
      section_title: 章节标题过滤词（可选，留空返回所有章节）。
                     支持大小写不敏感的子字符串匹配，例如 "架构" 匹配 "整体架构设计"。

    返回章节列表，每个章节包含完整的页面内容（Markdown）、关联文件和重要度。
    """
    try:
        async with async_session_factory() as db:
            wiki_result = await db.execute(
                select(Wiki)
                .where(Wiki.repo_id == repo_id)
                .options(
                    selectinload(Wiki.sections).selectinload(WikiSection.pages)
                )
                .order_by(Wiki.created_at.desc())
                .limit(1)
            )
            wiki: Optional[Wiki] = wiki_result.scalar_one_or_none()

        if wiki is None:
            return [{"error": f"仓库 {repo_id} 尚无 Wiki 内容", "tool": "get_wiki_content"}]

        filter_lower = section_title.strip().lower()

        results = []
        for section in wiki.sections:
            # 章节标题过滤（大小写不敏感子串匹配）
            if filter_lower and filter_lower not in section.title.lower():
                continue

            pages_data = []
            for page in section.pages:
                pages_data.append({
                    "title": page.title,
                    "content_md": page.content_md or "",
                    "relevant_files": page.relevant_files or [],
                    "importance": page.importance or "medium",
                })

            results.append({
                "section_title": section.title,
                "pages": pages_data,
            })

        if filter_lower and not results:
            return [{"error": f"未找到匹配章节: '{section_title}'", "tool": "get_wiki_content"}]

        return results

    except Exception as e:
        logger.error(f"[get_wiki_content] repo={repo_id}, 错误: {e}")
        return [{"error": str(e), "tool": "get_wiki_content"}]


# ---------------------------------------------------------------------------
# Tool 7: get_dependency_graph
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_dependency_graph(repo_id: str, file_path: str = "") -> dict:
    """
    获取代码库（或指定文件）的依赖调用图。

    参数:
      repo_id:   仓库 ID
      file_path: 文件路径前缀过滤（可选，留空返回整个仓库图）。
                 例如 "app/services/" 只返回该目录下的节点。

    返回有向图数据：nodes（代码节点）和 edges（调用关系）。
    注意：仅对不超过约 5000 个 chunk 的仓库准确。
    """
    try:
        collection = get_collection(repo_id)
        count = collection.count()

        if count == 0:
            return {
                "nodes": [],
                "edges": [],
                "total_nodes": 0,
                "total_edges": 0,
                "warning": "仓库尚无向量化数据",
            }

        # 获取全部 chunk 元数据
        all_data = collection.get(include=["metadatas"])
        ids: list[str] = all_data.get("ids", [])
        metadatas: list[dict] = all_data.get("metadatas", []) or []

        # 构建 name -> chunk_id 映射（用于解析 calls 边）
        name_to_ids: dict[str, list[str]] = {}
        for chunk_id, meta in zip(ids, metadatas):
            name = meta.get("name", "")
            if name:
                name_to_ids.setdefault(name, []).append(chunk_id)

        # 过滤节点
        filter_prefix = file_path.strip()
        nodes: list[dict] = []
        chunk_id_set: set[str] = set()

        for chunk_id, meta in zip(ids, metadatas):
            chunk_file = meta.get("file_path", "")
            if filter_prefix and not chunk_file.startswith(filter_prefix):
                continue

            nodes.append({
                "id": chunk_id,
                "name": meta.get("name", ""),
                "file": chunk_file,
                "type": meta.get("node_type", ""),
                "start_line": int(meta.get("start_line", 0)),
                "end_line": int(meta.get("end_line", 0)),
                "language": meta.get("language", ""),
            })
            chunk_id_set.add(chunk_id)

        # 构建 edges
        edges: list[dict] = []
        for chunk_id, meta in zip(ids, metadatas):
            if chunk_id not in chunk_id_set:
                continue

            calls_raw: str = meta.get("calls", "") or ""
            if not calls_raw.strip():
                continue

            call_names = [c.strip() for c in calls_raw.split(",") if c.strip()]
            for call_name in call_names:
                targets = name_to_ids.get(call_name, [])
                for target_id in targets:
                    # 如果有文件过滤，只加入图内节点之间的边
                    if filter_prefix and target_id not in chunk_id_set:
                        continue
                    if target_id == chunk_id:
                        continue
                    edges.append({
                        "from": chunk_id,
                        "to": target_id,
                        "type": "calls",
                        "call_name": call_name,
                    })

        return {
            "nodes": nodes,
            "edges": edges,
            "total_nodes": len(nodes),
            "total_edges": len(edges),
        }

    except Exception as e:
        logger.error(f"[get_dependency_graph] repo={repo_id}, 错误: {e}")
        return {"error": str(e), "tool": "get_dependency_graph"}


# ---------------------------------------------------------------------------
# Tool 8: list_files
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_files(
    repo_id: str,
    path_prefix: str = "",
    extensions: list[str] | None = None,
) -> list:
    """
    列出仓库中的文件列表（支持路径前缀和扩展名过滤）。

    参数:
      repo_id:      仓库 ID
      path_prefix:  路径前缀过滤（可选），例如 "app/" 或 "src/components/"
      extensions:   扩展名过滤列表（可选），例如 [".py", ".ts"]。
                    可省略点号，".py" 和 "py" 均可。

    返回相对文件路径列表，最多 500 个。
    """
    try:
        base_dir = os.path.join(settings.REPOS_BASE_DIR, repo_id)
        if not os.path.isdir(base_dir):
            return [{"error": f"仓库目录不存在: {repo_id}", "tool": "list_files"}]

        # 规范化扩展名（确保有前导点）
        normalized_exts: list[str] = []
        for ext in (extensions or []):
            ext = ext.strip()
            if ext and not ext.startswith("."):
                ext = "." + ext
            if ext:
                normalized_exts.append(ext.lower())

        prefix_normalized = path_prefix.replace("\\", "/").lstrip("/")

        collected: list[str] = []
        for dirpath, dirnames, filenames in os.walk(base_dir):
            # 跳过隐藏目录（如 .git）
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]

            for filename in filenames:
                full = os.path.join(dirpath, filename)
                # 计算相对路径，统一使用正斜杠
                rel = os.path.relpath(full, base_dir).replace("\\", "/")

                # 路径前缀过滤
                if prefix_normalized and not rel.startswith(prefix_normalized):
                    continue

                # 扩展名过滤
                if normalized_exts:
                    _, ext = os.path.splitext(filename)
                    if ext.lower() not in normalized_exts:
                        continue

                collected.append(rel)

                if len(collected) >= 500:
                    break

            if len(collected) >= 500:
                break

        collected.sort()
        return collected

    except Exception as e:
        logger.error(f"[list_files] repo={repo_id}, 错误: {e}")
        return [{"error": str(e), "tool": "list_files"}]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Open-DeepWiki MCP Server")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--port", type=int, default=8808)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        # HTTP 模式：使用 streamable-http 传输
        import uvicorn
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.requests import Request
        from starlette.responses import JSONResponse

        class BearerAuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                if settings.MCP_AUTH_TOKEN:
                    auth = request.headers.get("Authorization", "")
                    if not auth.startswith("Bearer ") or auth[7:] != settings.MCP_AUTH_TOKEN:
                        return JSONResponse({"error": "Unauthorized"}, status_code=401)
                return await call_next(request)

        starlette_app = mcp.streamable_http_app()
        if settings.MCP_AUTH_TOKEN:
            starlette_app.add_middleware(BearerAuthMiddleware)

        logger.info(f"[MCP] 启动 HTTP 模式，监听 {args.host}:{args.port}")
        uvicorn.run(starlette_app, host=args.host, port=args.port, log_config=None)


if __name__ == "__main__":
    main()
