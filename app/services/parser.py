"""
模块二：AST 解析服务存根。
此文件为 Module 1 提供的接口存根，真实实现在 Module 2 中完成。
"""
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.chunk_node import ChunkNode


async def parse_repository(
    db: AsyncSession,
    repo_id: str,
    local_path: str,
) -> List[ChunkNode]:
    """
    解析仓库中的所有代码文件，返回 ChunkNode 列表。
    [Module 2 存根 - 待实现]
    """
    raise NotImplementedError("模块二尚未实现：parse_repository")
