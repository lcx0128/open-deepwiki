import logging
from typing import Dict, List, Optional, Set

from app.schemas.chunk_node import ChunkNode
from app.services.embedder import get_collection

logger = logging.getLogger(__name__)


def build_dependency_graph(chunks: List[ChunkNode]) -> dict:
    """
    从 ChunkNode 列表构建依赖关系图。

    算法：
    1. 建立名称到 ChunkNode 的索引
    2. 遍历每个 chunk 的 calls 列表
    3. 如果 call 目标在索引中，建立有向边

    输出格式（邻接表）：
    {
        "nodes": [
            {"id": "chunk-uuid", "name": "func_name", "file": "src/a.py", "type": "function",
             "start_line": 10, "end_line": 35},
            ...
        ],
        "edges": [
            {"from": "chunk-uuid-1", "to": "chunk-uuid-2", "type": "calls"},
            ...
        ]
    }

    被模块五 MCP Server 的 get_dependency_graph 工具调用。
    """
    # 建立名称索引（注意同名函数可能存在于不同文件）
    name_index: Dict[str, List[ChunkNode]] = {}
    for chunk in chunks:
        if chunk.name and chunk.name != "<anonymous>":
            name_index.setdefault(chunk.name, []).append(chunk)

    nodes = []
    edges = []
    seen_edges: Set[tuple] = set()

    for chunk in chunks:
        nodes.append({
            "id": chunk.id,
            "name": chunk.name,
            "file": chunk.file_path,
            "type": chunk.node_type,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "language": chunk.language,
            "is_orm_model": chunk.is_orm_model,
        })

        for call_name in chunk.calls:
            targets = name_index.get(call_name, [])
            for target in targets:
                # 避免自引用
                if target.id == chunk.id:
                    continue
                edge_key = (chunk.id, target.id)
                if edge_key not in seen_edges:
                    edges.append({
                        "from": chunk.id,
                        "to": target.id,
                        "type": "calls",
                        "call_name": call_name,
                    })
                    seen_edges.add(edge_key)

    return {"nodes": nodes, "edges": edges}


def get_orm_models(chunks: List[ChunkNode]) -> List[dict]:
    """
    从 ChunkNode 列表中提取所有 ORM 模型定义。

    被模块三 ERD 生成器调用。

    返回格式：
    [
        {
            "name": "UserModel",
            "file": "src/models/user.py",
            "fields": [
                {"name": "id", "type": "Integer", "primary_key": True, "nullable": False, "foreign_key": None},
                ...
            ]
        },
        ...
    ]
    """
    orm_models = []
    for chunk in chunks:
        if chunk.is_orm_model and chunk.orm_fields is not None:
            orm_models.append({
                "name": chunk.name,
                "file": chunk.file_path,
                "start_line": chunk.start_line,
                "fields": chunk.orm_fields,
            })
    return orm_models


def get_file_summary(chunks: List[ChunkNode]) -> dict:
    """
    按文件汇总 chunk 统计信息。

    用于 Wiki 生成时了解代码库结构。

    返回格式：
    {
        "src/main.py": {
            "language": "python",
            "chunk_count": 10,
            "functions": ["main", "process", ...],
            "classes": ["Server", ...],
            "orm_models": ["UserModel", ...]
        },
        ...
    }
    """
    file_summary: Dict[str, dict] = {}

    for chunk in chunks:
        fp = chunk.file_path
        if fp not in file_summary:
            file_summary[fp] = {
                "language": chunk.language,
                "chunk_count": 0,
                "functions": [],
                "classes": [],
                "orm_models": [],
            }

        file_summary[fp]["chunk_count"] += 1

        if chunk.node_type in ("function_definition", "function_declaration",
                                "function_item", "method_declaration", "method_definition"):
            if chunk.name and chunk.name != "<anonymous>":
                file_summary[fp]["functions"].append(chunk.name)
        elif chunk.node_type in ("class_definition", "class_declaration"):
            if chunk.name and chunk.name != "<anonymous>":
                file_summary[fp]["classes"].append(chunk.name)
                if chunk.is_orm_model:
                    file_summary[fp]["orm_models"].append(chunk.name)

    return file_summary


async def get_callees(
    repo_id: str,
    symbol_name: str,
    file_path: Optional[str] = None,
) -> List[str]:
    """
    查询指定 symbol 调用的函数名列表（下游依赖）。

    当前 ChromaDB metadata.calls 仅保存逗号分隔的函数名，因此必须提供
    file_path 来定位 caller，避免同名函数导致跨文件误匹配。
    """
    if not file_path:
        return []

    try:
        collection = get_collection(repo_id)
        results = collection.get(
            where={"name": symbol_name, "file_path": file_path},
            include=["metadatas"],
            limit=1,
        )

        metadatas = results.get("metadatas") or []
        if not results.get("ids") or not metadatas:
            return []

        calls_str = metadatas[0].get("calls", "")
        if not calls_str:
            return []

        return [call.strip() for call in calls_str.split(",") if call.strip()]
    except Exception as exc:
        logger.debug(
            "[DepGraph] get_callees failed for %s@%s: %s",
            symbol_name,
            file_path,
            exc,
        )

    return []


async def get_callers(
    repo_id: str,
    symbol_name: str,
    file_path: Optional[str] = None,
) -> List[str]:
    """
    查询调用指定 symbol 的函数名列表（上游调用方）。

    由于 calls 字段不包含被调用函数路径，这里只能在目标 symbol 存在后扫描
    全部 chunk 的 metadata，并按函数名做近似匹配。大仓库直接跳过以保护性能。
    """
    if not file_path:
        return []

    try:
        collection = get_collection(repo_id)

        target_check = collection.get(
            where={"name": symbol_name, "file_path": file_path},
            include=["metadatas"],
            limit=1,
        )
        if not target_check.get("ids"):
            return []

        chunk_count = collection.count()
        if chunk_count > 5000:
            logger.warning(
                "[DepGraph] get_callers skipped: repo %s has %s chunks (>5000)",
                repo_id,
                chunk_count,
            )
            return []

        all_chunks = collection.get(include=["metadatas"])
        callers: List[str] = []
        seen = set()
        for metadata in all_chunks.get("metadatas") or []:
            calls = [call.strip() for call in metadata.get("calls", "").split(",")]
            if symbol_name not in calls:
                continue

            caller_name = metadata.get("name", "")
            if not caller_name or caller_name == symbol_name or caller_name in seen:
                continue
            callers.append(caller_name)
            seen.add(caller_name)
            if len(callers) >= 10:
                break

        return callers
    except Exception as exc:
        logger.debug(
            "[DepGraph] get_callers failed for %s@%s: %s",
            symbol_name,
            file_path,
            exc,
        )

    return []
