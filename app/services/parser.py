import hashlib
import logging
from pathlib import Path
from typing import List, Optional, Callable

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.schemas.chunk_node import ChunkNode
from app.models.file_state import FileState
from app.services.language_detector import detect_language, should_skip
from app.services.ast_parser import parse_file as ast_parse_file
from app.services.chunker import split_large_chunk

logger = logging.getLogger(__name__)


async def parse_repository(
    db: AsyncSession,
    repo_id: str,
    local_path: str,
    progress_callback: Optional[Callable] = None,
    commit_hash: str = "",
    force_full: bool = False,
) -> tuple[list, dict]:
    """
    解析整个仓库，返回 (chunks, file_hashes) 二元组。

    file_hashes: {relative_path: sha256_hash}，供 Embedder 在向量化成功后写入 FileState。
    FileState 不在此处写入——只有 Embedding 成功后才持久化，避免"解析成功-向量化失败"留下脏数据。
    """
    all_chunks: List[ChunkNode] = []
    file_hashes: dict[str, str] = {}  # relative_path → sha256，供 Embedder 写 FileState 用
    local_path_obj = Path(local_path)

    if not local_path_obj.exists():
        logger.error(f"仓库路径不存在: {local_path}")
        return []

    # 收集所有待处理文件
    code_files = []
    for file_path in local_path_obj.rglob("*"):
        if file_path.is_file():
            relative_path = str(file_path.relative_to(local_path_obj)).replace("\\", "/")
            full_path = str(file_path)
            if not should_skip(full_path) and detect_language(full_path) is not None:
                code_files.append((relative_path, full_path))

    total = len(code_files)
    logger.info(f"[Parser] 发现 {total} 个代码文件待解析，仓库: {repo_id}")

    for idx, (relative_path, full_path) in enumerate(code_files):
        try:
            language = detect_language(full_path)
            if not language:
                continue

            # 读取文件内容
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    source_code = f.read()
            except Exception as e:
                logger.warning(f"[Parser] 无法读取文件 {relative_path}: {e}")
                continue

            if not source_code.strip():
                continue

            # 计算文件 hash（用于去重检查）
            file_hash = hashlib.sha256(source_code.encode("utf-8")).hexdigest()

            # 检查 FileState 是否已处理过（幂等性）
            result = await db.execute(
                select(FileState).where(
                    FileState.repo_id == repo_id,
                    FileState.file_path == relative_path,
                )
            )
            existing_state = result.scalar_one_or_none()

            if existing_state and existing_state.file_hash == file_hash and not force_full:
                # 文件未变更，跳过解析（使用已有 chunk IDs）
                logger.debug(f"[Parser] 跳过未变更文件: {relative_path}")
                continue

            # AST 解析
            raw_chunks = ast_parse_file(relative_path, source_code, language)

            # 记录本文件的 hash，供 Embedder 成功后写 FileState
            file_hashes[relative_path] = file_hash

            # 滑动窗口切分大 chunk
            file_chunks = []
            for chunk in raw_chunks:
                file_chunks.extend(split_large_chunk(chunk))

            all_chunks.extend(file_chunks)
            logger.debug(f"[Parser] 解析完成: {relative_path} -> {len(file_chunks)} chunks")

        except Exception as e:
            logger.error(f"[Parser] 解析文件失败 {relative_path}: {e}", exc_info=True)
            continue

        # 进度回调
        if progress_callback and total > 0:
            pct = (idx + 1) / total * 100
            await progress_callback(
                pct,
                f"解析 AST: {relative_path} ({idx + 1}/{total})"
            )

    logger.info(f"[Parser] 仓库解析完成，共生成 {len(all_chunks)} 个 chunks")
    return all_chunks, file_hashes


async def parse_single_file(
    file_path: str,
    language: Optional[str] = None,
) -> List[ChunkNode]:
    """
    解析单个文件，返回 ChunkNode 列表。

    Args:
        file_path: 文件的绝对路径或相对路径
        language: 编程语言（可选，不提供时自动检测）

    Returns:
        ChunkNode 列表
    """
    if language is None:
        language = detect_language(file_path)

    if language is None:
        logger.warning(f"[Parser] 无法检测语言: {file_path}")
        return []

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            source_code = f.read()
    except Exception as e:
        logger.error(f"[Parser] 无法读取文件 {file_path}: {e}")
        return []

    if not source_code.strip():
        return []

    # 获取相对路径用于 chunk 的 file_path 字段
    relative_path = file_path.replace("\\", "/")

    raw_chunks = ast_parse_file(relative_path, source_code, language)

    # 应用滑动窗口切分
    all_chunks = []
    for chunk in raw_chunks:
        all_chunks.extend(split_large_chunk(chunk))

    return all_chunks
