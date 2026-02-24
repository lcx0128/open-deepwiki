from typing import List, Dict, Set
from app.schemas.chunk_node import ChunkNode


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
