import asyncio
import shutil
import time
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db
from app.models.task import Task, TaskStatus
from app.models.repository import Repository
from app.config import settings
from app.core.system_config import get_effective_config, update_system_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["system"])

# 终态状态集合
_TERMINAL_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
    TaskStatus.INTERRUPTED,
}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _mask_key(value: Optional[str]) -> str:
    """API key 脱敏：None/空→""，长度≤8→"****"，其他→"****"+最后4位"""
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return "****" + value[-4:]


def _build_config_response(raw: dict) -> dict:
    """将 get_effective_config() 返回的原始配置构造为脱敏响应结构。"""
    from app.core.system_config import load_system_config
    overrides = load_system_config()
    return {
        "is_customized": bool(overrides),
        "llm": {
            "default_provider": (raw.get("DEFAULT_LLM_PROVIDER", "") or "").lower(),
            "default_model": raw.get("DEFAULT_LLM_MODEL", ""),
            "openai_api_key": _mask_key(raw.get("OPENAI_API_KEY")),
            "openai_base_url": raw.get("OPENAI_BASE_URL") or "",
            "dashscope_api_key": _mask_key(raw.get("DASHSCOPE_API_KEY")),
            "google_api_key": _mask_key(raw.get("GOOGLE_API_KEY")),
            "custom_base_url": raw.get("CUSTOM_LLM_BASE_URL") or "",
            "custom_api_key": _mask_key(raw.get("CUSTOM_LLM_API_KEY")),
        },
        "embedding": {
            "api_key": _mask_key(raw.get("EMBEDDING_API_KEY")),
            "base_url": raw.get("EMBEDDING_BASE_URL") or "",
            "model": raw.get("EMBEDDING_MODEL") or "",
        },
        "wiki_language": raw.get("WIKI_LANGUAGE") or "",
    }


def _flatten_config_body(body: dict) -> dict:
    """将嵌套请求体拍平为 MANAGED_KEYS 格式。"""
    flat: dict = {}
    llm = body.get("llm", {})
    if isinstance(llm, dict):
        _maybe(flat, "DEFAULT_LLM_PROVIDER", llm.get("default_provider"))
        _maybe(flat, "DEFAULT_LLM_MODEL", llm.get("default_model"))
        _maybe(flat, "OPENAI_API_KEY", llm.get("openai_api_key"))
        _maybe(flat, "OPENAI_BASE_URL", llm.get("openai_base_url"))
        _maybe(flat, "DASHSCOPE_API_KEY", llm.get("dashscope_api_key"))
        _maybe(flat, "GOOGLE_API_KEY", llm.get("google_api_key"))
        _maybe(flat, "CUSTOM_LLM_BASE_URL", llm.get("custom_base_url"))
        _maybe(flat, "CUSTOM_LLM_API_KEY", llm.get("custom_api_key"))
    embedding = body.get("embedding", {})
    if isinstance(embedding, dict):
        _maybe(flat, "EMBEDDING_API_KEY", embedding.get("api_key"))
        _maybe(flat, "EMBEDDING_BASE_URL", embedding.get("base_url"))
        _maybe(flat, "EMBEDDING_MODEL", embedding.get("model"))
    if "wiki_language" in body:
        _maybe(flat, "WIKI_LANGUAGE", body.get("wiki_language"))
    return flat


def _maybe(d: dict, key: str, value) -> None:
    """只有 value 不为 None 时才写入字典（允许空字符串）。"""
    if value is not None:
        d[key] = value


def _get_dir_size(path: Path) -> int:
    """递归计算目录大小（bytes），忽略不可读文件。"""
    total = 0
    try:
        for entry in path.rglob("*"):
            try:
                if entry.is_file():
                    total += entry.stat().st_size
            except Exception:
                pass
    except Exception:
        pass
    return total


def _format_size(size_bytes: int) -> str:
    """将字节数格式化为可读字符串。"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
    else:
        return f"{size_bytes / 1024 ** 3:.2f} GB"


async def _scan_orphans(db: AsyncSession) -> dict:
    """扫描孤儿目录和孤儿 ChromaDB 集合，返回扫描结果。"""
    # 1. 查 DB 中所有 Repository.local_path（非空）
    repos_result = await db.execute(select(Repository))
    repos = repos_result.scalars().all()
    live_paths = set()
    live_repo_ids = set()
    for repo in repos:
        live_repo_ids.add(repo.id)
        if repo.local_path:
            try:
                live_paths.add(Path(repo.local_path).resolve())
            except Exception:
                pass

    # 2. 扫描 REPOS_BASE_DIR 下的直接子目录
    repos_base = Path(settings.REPOS_BASE_DIR)
    orphan_dirs = []
    if repos_base.exists():
        def _scan_dirs():
            result = []
            try:
                for child in repos_base.iterdir():
                    if child.is_dir():
                        try:
                            resolved = child.resolve()
                        except Exception:
                            resolved = child
                        if resolved not in live_paths:
                            size = _get_dir_size(child)
                            result.append({
                                "path": str(child),
                                "size_bytes": size,
                                "size_human": _format_size(size),
                            })
            except Exception:
                pass
            return result

        orphan_dirs = await asyncio.to_thread(_scan_dirs)

    # 3. 扫描孤儿 ChromaDB 集合
    # ChromaDB 集合名格式：repo_{repo_id}_chunks，其中 repo_id 的连字符替换为下划线
    live_collection_names = {
        f"repo_{rid.replace('-', '_')}_chunks" for rid in live_repo_ids
    }

    def _list_chromadb_orphans():
        try:
            import chromadb
            client = chromadb.PersistentClient(path=settings.CHROMADB_PATH)
            collections = client.list_collections()
            orphans = []
            for col in collections:
                col_name = col.name if hasattr(col, "name") else str(col)
                if col_name not in live_collection_names:
                    orphans.append(col_name)
            return orphans
        except Exception as e:
            logger.warning(f"[SystemAPI] ChromaDB 集合扫描失败: {e}")
            return []

    orphan_collections = await asyncio.to_thread(_list_chromadb_orphans)

    total_bytes = sum(d["size_bytes"] for d in orphan_dirs)
    return {
        "orphan_dirs": orphan_dirs,
        "orphan_chromadb_collections": orphan_collections,
        "total_reclaimable_bytes": total_bytes,
        "total_reclaimable_human": _format_size(total_bytes),
    }


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------

@router.get("/config", summary="获取当前生效配置（API key 脱敏）")
async def get_config():
    """返回当前生效的系统配置，API key 已脱敏处理。"""
    raw = await asyncio.to_thread(get_effective_config)
    return _build_config_response(raw)


@router.put("/config", summary="更新系统配置")
async def put_config(body: dict):
    """
    更新系统配置。body 结构与 GET /api/system/config 响应相同。
    以 "****" 开头的 API key 值会被自动跳过（未修改）。
    """
    flat = _flatten_config_body(body)
    if not flat:
        raise HTTPException(status_code=400, detail="请求体中没有可更新的配置项")
    await asyncio.to_thread(update_system_config, flat)
    raw = await asyncio.to_thread(get_effective_config)
    return _build_config_response(raw)


@router.get("/health", summary="系统健康检查")
async def get_health(db: AsyncSession = Depends(get_db)):
    """并发检查数据库、Redis、ChromaDB、Celery Worker 各服务状态。"""

    async def _check_database() -> dict:
        t0 = time.monotonic()
        try:
            await db.execute(select(func.count(Repository.id)))
            elapsed = round((time.monotonic() - t0) * 1000)
            return {"status": "ok", "latency_ms": elapsed}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def _check_redis() -> dict:
        t0 = time.monotonic()
        try:
            from app.core.redis_client import get_redis
            redis = await get_redis()
            await asyncio.wait_for(redis.ping(), timeout=2.0)
            elapsed = round((time.monotonic() - t0) * 1000)
            return {"status": "ok", "latency_ms": elapsed}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def _check_chromadb() -> dict:
        def _sync():
            try:
                import chromadb
                client = chromadb.PersistentClient(path=settings.CHROMADB_PATH)
                collections = client.list_collections()
                return {"status": "ok", "collection_count": len(collections)}
            except Exception as e:
                return {"status": "error", "error": str(e)}
        return await asyncio.to_thread(_sync)

    async def _check_worker() -> dict:
        def _sync():
            try:
                from app.celery_app import celery_app
                result = celery_app.control.inspect(timeout=2).ping()
                if result:
                    return {"status": "ok", "workers": len(result)}
                return {"status": "offline", "workers": 0}
            except Exception as e:
                return {"status": "error", "error": str(e)}
        return await asyncio.to_thread(_sync)

    # 并发执行所有检查
    db_status, redis_status, chromadb_status, worker_status = await asyncio.gather(
        _check_database(),
        _check_redis(),
        _check_chromadb(),
        _check_worker(),
    )

    # 统计
    total_repos_result = await db.execute(select(func.count(Repository.id)))
    total_repos = total_repos_result.scalar_one()

    total_tasks_result = await db.execute(select(func.count(Task.id)))
    total_tasks = total_tasks_result.scalar_one()

    active_tasks_result = await db.execute(
        select(func.count(Task.id)).where(
            ~Task.status.in_(list(_TERMINAL_STATUSES))
        )
    )
    active_tasks = active_tasks_result.scalar_one()

    # 判断整体状态：非 worker 的任意服务 error → degraded
    core_statuses = [db_status["status"], redis_status["status"], chromadb_status["status"]]
    overall = "degraded" if any(s == "error" for s in core_statuses) else "healthy"

    return {
        "status": overall,
        "services": {
            "database": db_status,
            "redis": redis_status,
            "chromadb": chromadb_status,
            "worker": worker_status,
        },
        "stats": {
            "total_repos": total_repos,
            "total_tasks": total_tasks,
            "active_tasks": active_tasks,
        },
    }


@router.get("/tasks", summary="获取任务列表")
async def list_tasks(
    page: int = Query(1, ge=1, description="页码"),
    per_page: int = Query(20, ge=1, le=100, description="每页数量"),
    status: Optional[str] = Query(None, description="按状态过滤"),
    db: AsyncSession = Depends(get_db),
):
    """分页获取所有任务，可按状态过滤，包含关联仓库名称。"""
    query = (
        select(Task, Repository.name.label("repo_name"))
        .join(Repository, Task.repo_id == Repository.id, isouter=True)
    )
    count_query = select(func.count(Task.id))

    if status:
        try:
            status_enum = TaskStatus(status)
            query = query.where(Task.status == status_enum)
            count_query = count_query.where(Task.status == status_enum)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效状态值: {status}")

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    offset = (page - 1) * per_page
    query = query.order_by(Task.created_at.desc()).offset(offset).limit(per_page)
    rows = await db.execute(query)

    items = []
    for row in rows.all():
        task: Task = row[0]
        repo_name: Optional[str] = row[1]
        items.append({
            "id": task.id,
            "repo_id": task.repo_id,
            "repo_name": repo_name,
            "type": task.type,
            "status": task.status,
            "progress_pct": task.progress_pct,
            "created_at": task.created_at,
            "error_msg": task.error_msg,
            "failed_at_stage": task.failed_at_stage,
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.post("/tasks/{task_id}/cancel", summary="取消指定任务")
async def cancel_task(task_id: str, db: AsyncSession = Depends(get_db)):
    """取消指定任务。已处于终态的任务返回 400。"""
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task.status in _TERMINAL_STATUSES:
        raise HTTPException(status_code=400, detail=f"任务已处于终态: {task.status}")

    # 设置 Redis 取消标志
    try:
        from app.core.redis_client import set_cancel_flag
        await set_cancel_flag(task_id)
    except Exception as e:
        logger.warning(f"[SystemAPI] 设置 Redis 取消标志失败: {e}")

    # 撤销 Celery 任务
    if task.celery_task_id:
        try:
            from app.celery_app import celery_app
            celery_app.control.revoke(task.celery_task_id, terminate=True, signal="SIGTERM")
        except Exception as e:
            logger.warning(f"[SystemAPI] 撤销 Celery 任务失败: {e}")

    task.status = TaskStatus.CANCELLED
    await db.commit()

    return {"message": "已取消"}


@router.get("/storage", summary="查询存储用量")
async def get_storage():
    """查询各存储目录的磁盘占用情况。"""
    repos_base = Path(settings.REPOS_BASE_DIR)
    chromadb_path = Path(settings.CHROMADB_PATH)

    # 提取 SQLite 文件路径
    db_url = settings.DATABASE_URL
    db_file_path: Optional[Path] = None
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if db_url.startswith(prefix):
            raw_path = db_url[len(prefix):]
            # 去掉开头的 ./ 并解析为绝对路径
            db_file_path = Path(raw_path).resolve()
            break

    async def _dir_info(path: Path) -> dict:
        size_bytes = await asyncio.to_thread(_get_dir_size, path)
        try:
            sub_count = await asyncio.to_thread(
                lambda: sum(1 for _ in path.iterdir() if _.is_dir()) if path.exists() else 0
            )
        except Exception:
            sub_count = 0
        return {
            "path": str(path),
            "exists": path.exists(),
            "size_bytes": size_bytes,
            "size_human": _format_size(size_bytes),
            "subdirectory_count": sub_count,
        }

    async def _file_info(path: Optional[Path]) -> dict:
        if path is None:
            return {"path": None, "exists": False, "size_bytes": 0, "size_human": "0 B"}
        try:
            size_bytes = await asyncio.to_thread(lambda: path.stat().st_size if path.exists() else 0)
        except Exception:
            size_bytes = 0
        return {
            "path": str(path),
            "exists": path.exists() if path else False,
            "size_bytes": size_bytes,
            "size_human": _format_size(size_bytes),
        }

    repos_info, chromadb_info, db_info = await asyncio.gather(
        _dir_info(repos_base),
        _dir_info(chromadb_path),
        _file_info(db_file_path),
    )

    return {
        "repos_dir": repos_info,
        "chromadb": chromadb_info,
        "database": db_info,
    }


@router.post("/cleanup/scan", summary="扫描孤儿数据（预览）")
async def cleanup_scan(db: AsyncSession = Depends(get_db)):
    """扫描不属于任何仓库的孤儿目录和 ChromaDB 集合（只读，不执行删除）。"""
    return await _scan_orphans(db)


@router.post("/cleanup/execute", summary="执行孤儿数据清理")
async def cleanup_execute(db: AsyncSession = Depends(get_db)):
    """重新扫描并删除孤儿目录和 ChromaDB 集合，返回清理结果。"""
    # 安全检查：存在活跃任务时拒绝清理，避免删除正在使用的目录
    active_count_result = await db.execute(
        select(func.count(Task.id)).where(~Task.status.in_(list(_TERMINAL_STATUSES)))
    )
    active_count = active_count_result.scalar_one()
    if active_count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"存在 {active_count} 个活跃任务，请等待所有任务完成后再执行清理",
        )

    scan = await _scan_orphans(db)

    from app.tasks.git_operations import _remove_readonly

    cleaned_dirs = 0
    reclaimed_bytes = 0

    def _delete_dirs(orphan_dirs: list) -> tuple[int, int]:
        _cleaned = 0
        _reclaimed = 0
        for entry in orphan_dirs:
            path = Path(entry["path"])
            try:
                if path.exists():
                    shutil.rmtree(path, onerror=_remove_readonly)
                    _cleaned += 1
                    _reclaimed += entry["size_bytes"]
                    logger.info(f"[SystemAPI] 已删除孤儿目录: {path}")
            except Exception as e:
                logger.warning(f"[SystemAPI] 删除孤儿目录失败 {path}: {e}")
        return _cleaned, _reclaimed

    cleaned_dirs, reclaimed_bytes = await asyncio.to_thread(
        _delete_dirs, scan["orphan_dirs"]
    )

    cleaned_collections = 0

    def _delete_collections(orphan_collections: list) -> int:
        _cleaned = 0
        try:
            import chromadb
            client = chromadb.PersistentClient(path=settings.CHROMADB_PATH)
            for col_name in orphan_collections:
                try:
                    client.delete_collection(col_name)
                    _cleaned += 1
                    logger.info(f"[SystemAPI] 已删除孤儿 ChromaDB 集合: {col_name}")
                except Exception as e:
                    logger.warning(f"[SystemAPI] 删除孤儿集合失败 {col_name}: {e}")
        except Exception as e:
            logger.warning(f"[SystemAPI] ChromaDB 客户端初始化失败: {e}")
        return _cleaned

    cleaned_collections = await asyncio.to_thread(
        _delete_collections, scan["orphan_chromadb_collections"]
    )

    return {
        "cleaned_dirs": cleaned_dirs,
        "cleaned_collections": cleaned_collections,
        "reclaimed_bytes": reclaimed_bytes,
        "reclaimed_human": _format_size(reclaimed_bytes),
    }
