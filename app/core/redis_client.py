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


async def set_cancel_flag(task_id: str, ttl: int = 3600) -> None:
    """设置任务取消标志（Redis Key，TTL 1小时），无需 DB 写权限"""
    redis = await get_redis()
    await redis.set(f"cancel:{task_id}", "1", ex=ttl)


async def check_cancel_flag(task_id: str) -> bool:
    """检查任务是否被标记为取消（Redis Key）"""
    redis = await get_redis()
    return bool(await redis.exists(f"cancel:{task_id}"))


async def clear_cancel_flag(task_id: str) -> None:
    """清除任务取消标志"""
    redis = await get_redis()
    await redis.delete(f"cancel:{task_id}")
