# app/services/two_stage_retriever.py
import logging
import os
from typing import List

from app.services.embedder import get_collection, embed_query
from app.schemas.mcp_types import CodeGuideline, FileContext
from app.config import settings

logger = logging.getLogger(__name__)


async def stage1_discovery(
    query: str,
    repo_id: str,
    top_k: int = 10,
) -> List[CodeGuideline]:
    """
    Stage 1 (Discovery): 返回轻量代码导引。

    只返回 chunk 的元数据（名称、路径、描述），不返回完整代码。
    这样做的目的是让 LLM 先了解有哪些相关代码，
    再决定需要查看哪些的完整内容。

    关键约束：必须使用 query_embeddings（预计算向量），不能用 query_texts，
    否则会触发 ChromaDB 内置的 384-dim 模型，导致维度不匹配错误。
    """
    collection = get_collection(repo_id)

    # 先将查询文本转换为向量
    query_vector = await embed_query(query)

    results = collection.query(
        query_embeddings=[query_vector],  # 使用预计算向量，不用 query_texts
        n_results=min(top_k, collection.count() or 1),
        include=["metadatas", "distances", "documents"],
    )

    guidelines = []
    if results["ids"] and results["ids"][0]:
        for i, chunk_id in enumerate(results["ids"][0]):
            metadata = results["metadatas"][0][i] if results["metadatas"] else {}
            distance = results["distances"][0][i] if results["distances"] else 0.0
            doc = results["documents"][0][i] if results["documents"] else ""

            # 从 document 中提取简短描述（首行或 docstring）
            first_line = doc.split("\n")[0][:100] if doc else ""

            guidelines.append(CodeGuideline(
                chunk_id=chunk_id,
                name=metadata.get("name", ""),
                file_path=metadata.get("file_path", ""),
                node_type=metadata.get("node_type", ""),
                start_line=int(metadata.get("start_line", 0)),
                end_line=int(metadata.get("end_line", 0)),
                description=first_line,
                relevance_score=max(0.0, 1.0 - float(distance)),  # 距离转相似度
            ))

    logger.debug(f"[Stage1] repo={repo_id}, query='{query[:50]}', found={len(guidelines)}")
    return guidelines


async def stage2_assembly(
    chunk_ids: List[str],
    repo_id: str,
) -> List[str]:
    """
    Stage 2 (Assembly): 获取指定 chunk 的完整代码内容。

    由 LLM 在 Stage 1 后判断需要哪些 chunk，
    然后调用此方法获取完整代码。
    """
    if not chunk_ids:
        return []

    collection = get_collection(repo_id)

    results = collection.get(
        ids=chunk_ids,
        include=["documents", "metadatas"],
    )

    contents = []
    if results["documents"]:
        for i, doc in enumerate(results["documents"]):
            metadata = results["metadatas"][i] if results["metadatas"] else {}
            file_path = metadata.get("file_path", "unknown")
            start_line = metadata.get("start_line", 0)
            end_line = metadata.get("end_line", 0)
            contents.append(
                f"// File: {file_path} (Lines {start_line}-{end_line})\n{doc}"
            )

    logger.debug(f"[Stage2] repo={repo_id}, fetched={len(contents)} chunks")
    return contents


def read_file_context(
    repo_id: str,
    file_path: str,
    start_line: int,
    end_line: int,
) -> FileContext:
    """
    直接从文件系统读取指定范围的代码。

    用于 MCP read_file_context 工具和 Stage 2 按需加载。
    行号使用 1-indexed。
    """
    from app.services.language_detector import detect_language

    full_path = os.path.join(settings.REPOS_BASE_DIR, repo_id, file_path)
    if not os.path.exists(full_path):
        raise FileNotFoundError(f"文件不存在: {file_path} (repo: {repo_id})")

    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    # 行号转换（1-indexed 到 0-indexed）
    start = max(0, start_line - 1)
    end = min(len(lines), end_line)
    selected_lines = lines[start:end]

    return FileContext(
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        content="".join(selected_lines),
        language=detect_language(file_path) or "text",
    )
