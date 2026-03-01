import asyncio
import json
import logging
import uuid
from typing import List, Callable, Optional, Dict

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import chromadb
from openai import AsyncOpenAI, RateLimitError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.core.system_config import get_effective_config
from app.schemas.chunk_node import ChunkNode
from app.models.file_state import FileState

logger = logging.getLogger(__name__)

# 并发信号量：限制同时进行的 Embedding API 调用数（懒加载，避免跨事件循环问题）
_embedding_semaphore: Optional[asyncio.Semaphore] = None

# ChromaDB 客户端（持久化到本地目录）
_chroma_client: Optional[chromadb.PersistentClient] = None

# OpenAI 客户端（用于 Embedding）
_openai_client: Optional[AsyncOpenAI] = None
# 上次创建客户端时使用的配置签名（api_key, base_url），用于检测配置变更后自动重建客户端
_last_embedding_config: Optional[tuple] = None


def _get_chroma_client() -> chromadb.PersistentClient:
    """懒加载 ChromaDB 客户端"""
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=settings.CHROMADB_PATH)
    return _chroma_client


def _get_openai_client() -> AsyncOpenAI:
    """懒加载 OpenAI 客户端（Embedding 专用）

    优先使用 EMBEDDING_API_KEY / EMBEDDING_BASE_URL，
    未配置时回退到 OPENAI_API_KEY / OPENAI_BASE_URL。
    这样可以用平台 A 做 Wiki 生成（LLM），用平台 B 做向量提取（Embedding）。
    配置优先级：system_config.json > .env 文件
    当 Embedding 相关配置发生变更时自动重建客户端。
    """
    global _openai_client, _last_embedding_config
    config = get_effective_config()
    api_key = config["EMBEDDING_API_KEY"] or config["OPENAI_API_KEY"] or "dummy-key"
    base_url = config["EMBEDDING_BASE_URL"] or config["OPENAI_BASE_URL"]
    current_sig = (api_key, base_url)
    if _openai_client is None or current_sig != _last_embedding_config:
        _openai_client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        _last_embedding_config = current_sig
    return _openai_client


def _get_semaphore() -> asyncio.Semaphore:
    """懒加载 Semaphore，确保在当前事件循环上下文中创建"""
    global _embedding_semaphore
    if _embedding_semaphore is None:
        _embedding_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_LLM_CALLS)
    return _embedding_semaphore


def get_collection(repo_id: str) -> chromadb.Collection:
    """获取或创建 ChromaDB collection"""
    client = _get_chroma_client()
    collection_name = f"repo_{repo_id}_chunks"
    # ChromaDB collection 名称只允许字母数字和下划线
    collection_name = collection_name.replace("-", "_")
    return client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},  # 余弦相似度
    )


def delete_collection(repo_id: str) -> None:
    """删除 ChromaDB collection（仓库删除时调用）"""
    client = _get_chroma_client()
    collection_name = f"repo_{repo_id}_chunks".replace("-", "_")
    try:
        client.delete_collection(collection_name)
    except Exception:
        pass  # 集合不存在时忽略


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type((RateLimitError, TimeoutError, ConnectionError)),
    before_sleep=lambda state: logger.warning(
        f"[Embedding] 重试第 {state.attempt_number} 次，等待 {state.next_action.sleep} 秒"
    ),
)
async def _call_embedding_api(texts: List[str]) -> List[List[float]]:
    """
    调用 Embedding API（带信号量限制和指数退避重试）。

    关键防御措施：
    1. asyncio.Semaphore 限制并发数，防止 429
    2. tenacity 指数退避重试：2s -> 4s -> 8s
    3. 最多重试 3 次后放弃
    """
    async with _get_semaphore():
        client = _get_openai_client()
        config = get_effective_config()
        response = await client.embeddings.create(
            model=config["EMBEDDING_MODEL"],
            input=texts,
        )
        return [item.embedding for item in response.data]


async def embed_chunks(
    db: AsyncSession,
    repo_id: str,
    chunks: List[ChunkNode],
    file_hashes: Optional[Dict[str, str]] = None,
    commit_hash: str = "",
    progress_callback: Optional[Callable] = None,
) -> List[str]:
    """
    将 ChunkNode 列表向量化并写入 ChromaDB。

    处理流程：
    1. 将 chunks 按批次分组（每批 50 个）
    2. 对每批调用 Embedding API
    3. 写入 ChromaDB
    4. 按 file_path 分组，更新 FileState 表的 chunk_ids_json

    返回：所有 chunk ID 列表
    """
    if not chunks:
        return []

    collection = get_collection(repo_id)
    all_chunk_ids = []
    batch_size = 10  # DashScope text-embedding-v3 单批上限为 10
    total = len(chunks)

    for i in range(0, total, batch_size):
        batch = chunks[i:i + batch_size]

        # 准备 Embedding 输入文本
        texts = [chunk.to_embedding_text() for chunk in batch]
        ids = [chunk.id for chunk in batch]
        metadatas = [chunk.to_metadata() for chunk in batch]

        # 调用 Embedding API（失败时向上抛出，由调用方决定是否标记任务失败）
        try:
            embeddings = await _call_embedding_api(texts)
        except Exception as e:
            logger.error(f"[Embedder] Embedding API 调用失败（批次 {i // batch_size + 1}）: {e}")
            raise

        # 写入 ChromaDB（带向量）
        collection.add(
            ids=ids,
            documents=[chunk.content for chunk in batch],
            metadatas=metadatas,
            embeddings=embeddings,
        )

        all_chunk_ids.extend(ids)

        # 进度回调
        if progress_callback:
            pct = min((i + batch_size) / total * 100, 100)
            await progress_callback(pct, f"向量化进度: {min(i + batch_size, total)}/{total}")

    # 向量化全部成功后，才写入 FileState（防止 embedding 失败留下脏数据）
    if file_hashes is not None:
        file_chunks: Dict[str, List[str]] = {}
        for chunk in chunks:
            file_chunks.setdefault(chunk.file_path, []).append(chunk.id)

        for file_path, chunk_ids in file_chunks.items():
            result = await db.execute(
                select(FileState).where(
                    FileState.repo_id == repo_id,
                    FileState.file_path == file_path,
                )
            )
            fs = result.scalar_one_or_none()
            file_hash = file_hashes.get(file_path, "")
            if fs:
                fs.file_hash = file_hash
                fs.chunk_ids_json = json.dumps(chunk_ids)
                fs.chunk_count = len(chunk_ids)
                if commit_hash:
                    fs.last_commit_hash = commit_hash
            else:
                db.add(FileState(
                    id=str(uuid.uuid4()),
                    repo_id=repo_id,
                    file_path=file_path,
                    last_commit_hash=commit_hash,
                    file_hash=file_hash,
                    chunk_ids_json=json.dumps(chunk_ids),
                    chunk_count=len(chunk_ids),
                ))

        await db.flush()
    return all_chunk_ids


async def embed_query(text: str) -> List[float]:
    """
    将查询文本转换为 embedding 向量（用于 RAG 检索）。

    与 embed_chunks 使用相同的 Embedding 模型，确保向量空间一致。
    单条文本，无需批处理。
    """
    embeddings = await _call_embedding_api([text])
    return embeddings[0]
