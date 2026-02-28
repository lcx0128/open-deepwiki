import asyncio
import json
import subprocess
from enum import Enum
from typing import List, Tuple, Optional, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.models.file_state import FileState


class FileChangeType(str, Enum):
    ADDED = "A"
    MODIFIED = "M"
    DELETED = "D"
    RENAMED = "R"


def _detect_changed_files_sync(repo_path: str, branch: str = "main") -> List[Tuple[str, str]]:
    """
    检测仓库中自上次同步以来的文件变更（同步实现，由 asyncio.to_thread 包装调用）。

    返回: [(变更类型, 文件路径), ...]
    - 'A': 新增文件
    - 'M': 修改文件
    - 'D': 删除文件
    - 重命名 (R) 拆分为 D(旧路径) + A(新路径)

    算法步骤：
    1. git fetch origin {branch} — 拉取远程最新引用
    2. git diff HEAD..origin/{branch} --name-status — 对比本地与远程
    3. 解析输出，提取变更类型和文件路径
    """
    # Step 1: Fetch 远程更新
    fetch_result = subprocess.run(
        ["git", "fetch", "origin", branch],
        cwd=repo_path,
        capture_output=True,
        timeout=120,
    )
    if fetch_result.returncode != 0:
        raise RuntimeError(f"git fetch 失败: {fetch_result.stderr.decode(errors='replace')}")

    # Step 2: Diff 检测变更
    result = subprocess.run(
        ["git", "diff", f"HEAD..origin/{branch}", "--name-status"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        raise RuntimeError(f"git diff 失败: {result.stderr}")

    # Step 3: 解析输出
    # 格式:
    #   A\tnew_file
    #   M\tmodified_file
    #   D\tdeleted_file
    #   R100\told_file\tnew_file  (重命名，相似度 100%)
    changes = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        raw_type = parts[0][0]  # 取首字符: A/M/D/R/C/...
        if raw_type == "R" and len(parts) >= 3:
            # 重命名：拆分为删除旧路径 + 新增新路径，各自独立处理
            old_path = parts[1]
            new_path = parts[2]
            changes.append(("D", old_path))
            changes.append(("A", new_path))
        else:
            changes.append((raw_type, parts[-1]))

    return changes


def _git_merge_ff_only_sync(repo_path: str, branch: str) -> None:
    """执行 git merge --ff-only（同步，由 asyncio.to_thread 包装调用）"""
    result = subprocess.run(
        ["git", "merge", f"origin/{branch}", "--ff-only"],
        cwd=repo_path,
        capture_output=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git merge --ff-only 失败（本地分支可能已分叉）: "
            f"{result.stderr.decode(errors='replace')}"
        )


async def apply_incremental_sync(
    db: AsyncSession,
    repo_id: str,
    repo_path: str,
    chromadb_collection: Any,
    branch: str = "main",
) -> dict:
    """
    执行增量同步的核心逻辑。

    返回: {"added": int, "modified": int, "deleted": int, "unchanged": int}
    """
    # 在线程池中运行阻塞的 git 操作，避免阻塞 asyncio 事件循环
    changes = await asyncio.to_thread(_detect_changed_files_sync, repo_path, branch)
    stats = {"added": 0, "modified": 0, "deleted": 0, "unchanged": 0}
    changed_paths = []

    for change_type, file_path in changes:
        changed_paths.append(file_path)
        if change_type == "D":
            # 删除：清理 ChromaDB chunks 和 FileState 记录
            await _delete_file_chunks(db, repo_id, file_path, chromadb_collection)
            stats["deleted"] += 1
        elif change_type == "M":
            # 修改：先删旧 chunks（重新解析由调用方负责）
            await _delete_file_chunks(db, repo_id, file_path, chromadb_collection)
            stats["modified"] += 1
        elif change_type == "A":
            # 新增：无旧 chunks 需删除
            stats["added"] += 1

    stats["changed_paths"] = changed_paths

    # 合并本地到远程版本（非阻塞）
    await asyncio.to_thread(_git_merge_ff_only_sync, repo_path, branch)

    # 增量更新代码库索引（仅更新变更文件的条目）
    if changes:
        try:
            from app.services.codebase_indexer import update_codebase_index_for_files
            # 复用已收集的 changed_paths，避免变量遮蔽
            await update_codebase_index_for_files(repo_id, db, changed_paths)
        except Exception as _idx_err:
            import logging as _logging
            _logging.getLogger(__name__).warning(f"[IncrementalSync] 更新代码库索引失败: {_idx_err}")

    return stats


async def _delete_file_chunks(
    db: AsyncSession,
    repo_id: str,
    file_path: str,
    chromadb_collection: Any,
) -> None:
    """删除单个文件在 ChromaDB 中的所有 chunks"""
    result = await db.execute(
        select(FileState).where(
            FileState.repo_id == repo_id,
            FileState.file_path == file_path,
        )
    )
    file_state = result.scalar_one_or_none()
    if not file_state:
        return

    chunk_ids = json.loads(file_state.chunk_ids_json)
    if chunk_ids and chromadb_collection is not None:
        chromadb_collection.delete(ids=chunk_ids)

    await db.execute(
        delete(FileState).where(FileState.id == file_state.id)
    )
    await db.flush()


async def process_files_with_idempotency(
    db: AsyncSession,
    repo_id: str,
    file_paths: List[str],
    current_commit_hash: str,
    chromadb_collection: Any,
    parse_file_fn: Any,
    embed_and_store_fn: Any,
) -> None:
    """
    幂等文件处理：通过 FileState 表判断文件是否已处理过。

    幂等性保证原理：
    1. 处理每个文件前，先查 FileState 表
    2. 如果 last_commit_hash == current_commit_hash，说明已处理，跳过
    3. 如果不等或不存在，执行处理
    4. 处理完成后立即更新/创建 FileState（事务性写入）
    """
    for file_path in file_paths:
        result = await db.execute(
            select(FileState).where(
                FileState.repo_id == repo_id,
                FileState.file_path == file_path,
            )
        )
        existing = result.scalar_one_or_none()

        # 幂等检查：如果已处理到当前 commit，跳过
        if existing and existing.last_commit_hash == current_commit_hash:
            continue

        # 如果有旧数据，先清理
        if existing:
            old_chunk_ids = json.loads(existing.chunk_ids_json)
            if old_chunk_ids and chromadb_collection is not None:
                chromadb_collection.delete(ids=old_chunk_ids)

        # 执行 AST 解析（由模块二提供）
        chunks = parse_file_fn(file_path)

        # 生成 embeddings 并写入 ChromaDB
        chunk_ids = embed_and_store_fn(chunks, chromadb_collection)

        # 原子性更新 FileState
        if existing:
            existing.last_commit_hash = current_commit_hash
            existing.chunk_ids_json = json.dumps(chunk_ids)
            existing.chunk_count = len(chunk_ids)
        else:
            new_state = FileState(
                repo_id=repo_id,
                file_path=file_path,
                last_commit_hash=current_commit_hash,
                chunk_ids_json=json.dumps(chunk_ids),
                chunk_count=len(chunk_ids),
            )
            db.add(new_state)

        await db.flush()


def _get_pending_commits_sync(repo_path: str, branch: str = "main") -> list:
    """
    获取本地 HEAD 到 origin/{branch} 之间的提交列表（同步实现）。
    先 git fetch，再 git log HEAD..origin/{branch}。
    返回: [{"hash": str, "short_hash": str, "message": str, "author": str, "date": str}, ...]
    """
    # Step 1: fetch 远程（静默，不报错）
    subprocess.run(
        ["git", "fetch", "origin", branch],
        cwd=repo_path,
        capture_output=True,
        timeout=120,
    )

    # Step 2: git log（使用 ASCII RS \x1e 作分隔符，避免提交信息中的 | 破坏解析）
    # encoding="utf-8" 是必须的：Git for Windows 输出 UTF-8，Windows 默认 ANSI 码页会导致解码失败
    result = subprocess.run(
        ["git", "log", f"HEAD..origin/{branch}",
         "--format=%H\x1e%s\x1e%an\x1e%ad", "--date=short"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    commits = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\x1e", 3)
        if len(parts) < 4:
            continue
        full_hash, message, author, date = parts
        commits.append({
            "hash": full_hash,
            "short_hash": full_hash[:7],
            "message": message,
            "author": author,
            "date": date,
        })
    return commits


async def get_pending_commits(repo_path: str, branch: str = "main") -> list:
    """
    异步包装：获取远程仓库自上次同步以来的新提交。
    """
    return await asyncio.to_thread(_get_pending_commits_sync, repo_path, branch)
