"""
模块二：向量嵌入服务存根。
此文件为 Module 1 提供的接口存根，真实实现在 Module 2 中完成。
"""
from typing import List, Callable, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.chunk_node import ChunkNode


async def embed_chunks(
    db: AsyncSession,
    repo_id: str,
    chunks: List[ChunkNode],
    progress_callback: Optional[Callable] = None,
) -> List[str]:
    """
    将 ChunkNode 列表向量化并存入 ChromaDB，返回 chunk ID 列表。
    [Module 2 存根 - 待实现]
    """
    raise NotImplementedError("模块二尚未实现：embed_chunks")
