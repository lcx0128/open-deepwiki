"""
模块三：Wiki 生成服务存根。
此文件为 Module 1 提供的接口存根，真实实现在 Module 3 中完成。
"""
from typing import Optional, Callable
from sqlalchemy.ext.asyncio import AsyncSession


async def generate_wiki(
    db: AsyncSession,
    repo_id: str,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
    progress_callback: Optional[Callable] = None,
) -> str:
    """
    基于解析结果生成 Wiki 文档，返回 wiki_id。
    [Module 3 存根 - 待实现]
    """
    raise NotImplementedError("模块三尚未实现：generate_wiki")
