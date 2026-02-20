import json
import redis.asyncio as aioredis
from app.config import settings

_redis_pool: aioredis.Redis | None = None


async def init_redis() -> None:
    """初始化 Redis 连接池"""
    global _redis_pool
    _redis_pool = aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        max_connections=20,
    )
    # 验证连接
    await _redis_pool.ping()


async def get_redis() -> aioredis.Redis:
    """获取 Redis 客户端实例"""
    if _redis_pool is None:
        raise RuntimeError("Redis 未初始化，请检查 Lifespan 配置")
    return _redis_pool


async def close_redis() -> None:
    """关闭 Redis 连接池"""
    global _redis_pool
    if _redis_pool:
        await _redis_pool.aclose()
        _redis_pool = None


async def publish_progress(task_id: str, data: dict) -> None:
    """发布任务进度到 Redis Pub/Sub 频道"""
    redis = await get_redis()
    channel = f"task_progress:{task_id}"
    await redis.publish(channel, json.dumps(data, ensure_ascii=False))
