import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.embedder import get_collection

logger = logging.getLogger(__name__)

# Node types that map to functions
_FUNCTION_TYPES = {"function", "method", "arrow"}

# Node types that map to classes
_CLASS_TYPES = {"class", "interface", "struct", "impl"}

# Node types to skip entirely
_SKIP_TYPES = {"import_statement", "import_from_statement"}

# Names to skip
_SKIP_NAMES = {"", "<anonymous>"}


def _classify_node(node_type: str) -> Optional[str]:
    """Return 'functions', 'classes', 'constants', or None to skip."""
    if node_type in _SKIP_TYPES:
        return None
    if node_type == "constant":
        return "constants"
    for ft in _FUNCTION_TYPES:
        if ft in node_type:
            return "functions"
    for ct in _CLASS_TYPES:
        if ct in node_type:
            return "classes"
    return None


def _build_file_entry(metadatas: list) -> Dict[str, object]:
    """Build a single file index entry from a list of chunk metadatas for that file."""
    entry: Dict[str, object] = {"language": "", "functions": [], "classes": [], "constants": []}
    seen_names: Dict[str, set] = {"functions": set(), "classes": set(), "constants": set()}

    for meta in metadatas:
        name = meta.get("name", "")
        if name in _SKIP_NAMES:
            continue
        node_type = meta.get("node_type", "")
        if not entry["language"]:
            entry["language"] = meta.get("language", "")
        bucket = _classify_node(node_type)
        if bucket is None:
            continue
        if name not in seen_names[bucket]:
            seen_names[bucket].add(name)
            entry[bucket].append(name)  # type: ignore[union-attr]

    return entry


async def generate_codebase_index(repo_id: str, db: AsyncSession) -> Dict[str, object]:
    """
    Full regeneration of the codebase index from ChromaDB.

    Reads all chunk metadatas and builds a dict:
      {file_path: {language, functions, classes, constants}}

    Upserts a RepoIndex row and commits.
    Returns the index dict.
    """
    from app.models.repo_index import RepoIndex

    collection = get_collection(repo_id)
    result = collection.get(limit=2000, include=["metadatas"])
    metadatas = result.get("metadatas") or []

    # Group metadatas by file_path
    by_file: Dict[str, list] = {}
    for meta in metadatas:
        fp = meta.get("file_path", "")
        if fp:
            by_file.setdefault(fp, []).append(meta)

    # Build index
    index: Dict[str, object] = {}
    for fp, metas in by_file.items():
        index[fp] = _build_file_entry(metas)

    # Upsert RepoIndex row
    existing = await db.get(RepoIndex, repo_id)
    if existing:
        existing.index_json = index
        existing.generated_at = datetime.now(timezone.utc)
    else:
        db.add(RepoIndex(
            repo_id=repo_id,
            index_json=index,
            generated_at=datetime.now(timezone.utc),
        ))

    await db.commit()
    logger.info(f"[CodebaseIndexer] 索引生成完成: repo_id={repo_id}, 文件数={len(index)}")
    return index


async def update_codebase_index_for_files(
    repo_id: str,
    db: AsyncSession,
    updated_file_paths: List[str],
) -> None:
    """
    Incremental update: rebuild index entries only for the given file paths.

    If no existing index is found, falls back to a full regeneration.
    """
    from app.models.repo_index import RepoIndex

    if not updated_file_paths:
        return

    existing = await db.get(RepoIndex, repo_id)
    if existing is None or existing.index_json is None:
        await generate_codebase_index(repo_id, db)
        return

    index: Dict[str, object] = dict(existing.index_json)

    # Remove stale entries for the changed files
    for fp in updated_file_paths:
        index.pop(fp, None)

    # Rebuild entries from ChromaDB for each changed file
    collection = get_collection(repo_id)
    for fp in updated_file_paths:
        try:
            result = collection.get(where={"file_path": fp}, include=["metadatas"])
            metas = result.get("metadatas") or []
            if metas:
                index[fp] = _build_file_entry(metas)
            # If no metadatas, the file was deleted — entry already removed above
        except Exception as e:
            logger.warning(f"[CodebaseIndexer] 无法查询文件 {fp} 的 chunks: {e}")

    existing.index_json = index
    existing.generated_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info(
        f"[CodebaseIndexer] 增量更新完成: repo_id={repo_id}, "
        f"更新文件数={len(updated_file_paths)}, 总文件数={len(index)}"
    )


def format_codebase_index(index: Dict[str, object], max_files: int = 80) -> str:
    """
    Format the codebase index as a compact string for injection into prompts.

    Example output:
        === CODEBASE INDEX ===
        app/services/chat_service.py
          Functions: handle_chat, handle_chat_stream | Constants: CHAT_SYSTEM_PROMPT
        ...
        === END CODEBASE INDEX ===
    """
    if not index:
        return ""

    lines = ["=== CODEBASE INDEX ==="]
    for i, (fp, entry) in enumerate(index.items()):
        if i >= max_files:
            remaining = len(index) - max_files
            lines.append(f"  ... (还有 {remaining} 个文件)")
            break

        if not isinstance(entry, dict):
            continue

        functions = entry.get("functions", [])[:15]
        classes = entry.get("classes", [])[:10]
        constants = entry.get("constants", [])[:10]

        parts = []
        if functions:
            parts.append(f"Functions: {', '.join(functions)}")
        if classes:
            parts.append(f"Classes: {', '.join(classes)}")
        if constants:
            parts.append(f"Constants: {', '.join(constants)}")

        lines.append(fp)
        if parts:
            lines.append(f"  {' | '.join(parts)}")

    lines.append("=== END CODEBASE INDEX ===")
    return "\n".join(lines)
