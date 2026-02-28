# app/services/two_stage_retriever.py
import logging
import os
import re
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

    # --- Keyword-based symbol lookup (hybrid search) ---
    COMMON_WORDS = {
        "the", "and", "for", "with", "that", "this", "from", "have", "will",
        "what", "how", "can", "are", "was", "but", "not", "you", "all",
        "been", "has", "had", "its", "into", "than", "then", "them",
        "who", "did", "get", "may", "new", "one", "our", "out", "say",
    }
    try:
        # Extract code identifiers: CamelCase, snake_case (≥3 chars), ALL_CAPS
        raw_symbols = re.findall(
            r'\b([A-Z][A-Za-z0-9]{2,}|[a-z][a-z0-9_]{2,}(?:_[a-z0-9]+)+|[A-Z][A-Z0-9_]{2,})\b',
            query,
        )
        symbols = [s for s in raw_symbols if s.lower() not in COMMON_WORDS]

        if symbols and collection.count() > 0:
            keyword_results = collection.get(
                where={"name": {"$in": symbols[:20]}},
                include=["metadatas", "documents", "ids"],
            )

            existing_ids = {g.chunk_id for g in guidelines}
            if keyword_results["ids"]:
                for i, chunk_id in enumerate(keyword_results["ids"]):
                    if chunk_id in existing_ids:
                        continue
                    metadata = keyword_results["metadatas"][i] if keyword_results["metadatas"] else {}
                    doc = keyword_results["documents"][i] if keyword_results["documents"] else ""
                    first_line = doc.split("\n")[0][:100] if doc else ""
                    guidelines.append(CodeGuideline(
                        chunk_id=chunk_id,
                        name=metadata.get("name", ""),
                        file_path=metadata.get("file_path", ""),
                        node_type=metadata.get("node_type", ""),
                        start_line=int(metadata.get("start_line", 0)),
                        end_line=int(metadata.get("end_line", 0)),
                        description=first_line,
                        relevance_score=0.85,
                    ))
                    existing_ids.add(chunk_id)

            # Cap total results and sort by relevance descending
            guidelines = sorted(guidelines, key=lambda g: g.relevance_score, reverse=True)
            guidelines = guidelines[:top_k * 2]

            logger.debug(
                f"[Stage1] keyword symbols={symbols[:5]}, keyword_hits="
                f"{len(keyword_results['ids']) if keyword_results['ids'] else 0}"
            )
    except Exception as exc:
        logger.warning(f"[Stage1] keyword lookup failed (non-fatal): {exc}")

    # --- Category-based node_type filter (3rd pass) ---
    # When query mentions category-level terms, fetch all chunks of that type directly
    CATEGORY_NODE_TYPE_MAP = {
        # Prompt / template / constant related
        "prompt": "constant", "提示词": "constant", "提示": "constant",
        "template": "constant", "模板": "constant", "模版": "constant",
        "constant": "constant", "常量": "constant", "全局变量": "constant",
        "config": "constant", "配置": "constant", "配置项": "constant",
        "setting": "constant", "settings": "constant", "env": "constant",
        "全局配置": "constant", "宏": "constant",
        # Class / model / schema related
        "class": "class_definition", "类": "class_definition",
        "model": "class_definition", "模型": "class_definition",
        "schema": "class_definition", "数据模型": "class_definition",
        "实体": "class_definition", "数据类": "class_definition",
        # Function / handler / endpoint related
        "function": "function_definition", "函数": "function_definition",
        "方法": "function_definition", "method": "function_definition",
        "handler": "function_definition", "处理器": "function_definition",
        "route": "function_definition", "路由": "function_definition",
        "endpoint": "function_definition", "接口": "function_definition",
        "service": "function_definition", "服务": "function_definition",
        "controller": "function_definition", "控制器": "function_definition",
        "middleware": "function_definition", "中间件": "function_definition",
        # Import / dependency related
        "import": "import_statement", "依赖": "import_statement",
        "dependency": "import_statement", "引用": "import_statement",
        # 文档类 (document sections)
        "readme": "document_section", "文档": "document_section",
        "说明": "document_section", "教程": "document_section",
        "手册": "document_section", "documentation": "document_section",
        "guide": "document_section", "介绍": "document_section",
        "overview": "document_section", "安装说明": "document_section",
        "install": "document_section", "使用说明": "document_section",
        "快速开始": "document_section", "功能说明": "document_section",
        "使用方法": "document_section", "changelog": "document_section",
        "更新日志": "document_section", "contributing": "document_section",
        "贡献": "document_section", "setup": "document_section",
        "部署说明": "document_section", "deployment": "document_section",
        # 配置文件类 (config files)
        "配置文件": "config_file", "package.json": "config_file",
        "docker": "config_file", "环境变量": "config_file",
        "npm": "config_file", "依赖配置": "config_file",
        "compose": "config_file", "dockerfile": "config_file",
    }
    try:
        query_lower = query.lower()
        target_node_types = set()
        for keyword, node_type in CATEGORY_NODE_TYPE_MAP.items():
            if keyword in query_lower:
                target_node_types.add(node_type)

        if target_node_types and collection.count() > 0:
            existing_ids = {g.chunk_id for g in guidelines}
            for node_type_val in target_node_types:
                category_results = collection.get(
                    where={"node_type": node_type_val},
                    include=["metadatas", "documents", "ids"],
                    limit=30,
                )
                if category_results["ids"]:
                    for i, chunk_id in enumerate(category_results["ids"]):
                        if chunk_id in existing_ids:
                            continue
                        metadata = category_results["metadatas"][i] if category_results["metadatas"] else {}
                        doc = category_results["documents"][i] if category_results["documents"] else ""
                        first_line = doc.split("\n")[0][:100] if doc else ""
                        guidelines.append(CodeGuideline(
                            chunk_id=chunk_id,
                            name=metadata.get("name", ""),
                            file_path=metadata.get("file_path", ""),
                            node_type=metadata.get("node_type", ""),
                            start_line=int(metadata.get("start_line", 0)),
                            end_line=int(metadata.get("end_line", 0)),
                            description=first_line,
                            relevance_score=0.80,
                        ))
                        existing_ids.add(chunk_id)

            guidelines = sorted(guidelines, key=lambda g: g.relevance_score, reverse=True)
            guidelines = guidelines[:top_k * 2]

            logger.debug(
                f"[Stage1] category_filter types={list(target_node_types)}, "
                f"total_guidelines={len(guidelines)}"
            )
    except Exception as exc:
        logger.warning(f"[Stage1] category filter failed (non-fatal): {exc}")

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


async def stage2_gap_fill_constants(
    retrieved_guidelines: List[CodeGuideline],
    repo_id: str,
) -> List[str]:
    """
    Round 2 gap-fill: 对 Stage 1 已检索的文件，从 ChromaDB 补取未命中的常量 chunk。

    解决两类问题：
    1. 向量语义距离导致常量未进入 top-K（如 WIKI_OUTLINE_PROMPT 语义与"prompt"距离远）
    2. 检索规划器按行读取文件时超出截断行数（如 PAGE_WRITER_PROMPT 在第 305 行）

    返回格式与 stage2_assembly 一致：["// File: path (Lines X-Y)\n{code}", ...]
    """
    if not retrieved_guidelines:
        return []

    collection = get_collection(repo_id)
    retrieved_names = {g.name for g in retrieved_guidelines}
    retrieved_files = list({g.file_path for g in retrieved_guidelines})[:5]

    gap_contents: List[str] = []

    for file_path in retrieved_files:
        try:
            const_results = collection.get(
                where={"file_path": file_path, "node_type": "constant"},
                include=["documents", "metadatas", "ids"],
            )
            if not const_results["ids"]:
                continue
            for i, chunk_id in enumerate(const_results["ids"]):
                metadata = const_results["metadatas"][i] if const_results["metadatas"] else {}
                name = metadata.get("name", "")
                if not name or name in retrieved_names:
                    continue
                doc = const_results["documents"][i] if const_results["documents"] else ""
                fp = metadata.get("file_path", file_path)
                sl = metadata.get("start_line", 0)
                el = metadata.get("end_line", 0)
                gap_contents.append(f"// File: {fp} (Lines {sl}-{el})\n{doc}")
                retrieved_names.add(name)
        except Exception:
            continue

    if gap_contents:
        logger.info(f"[Stage2GapFill] repo={repo_id}, filled={len(gap_contents)} missing constant chunks")

    return gap_contents
