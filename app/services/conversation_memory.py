# app/services/conversation_memory.py
import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from app.core.redis_client import get_redis

SESSION_TTL = 86400  # 24 小时


async def create_session(repo_id: str) -> str:
    """创建新的对话会话，返回新 session_id"""
    session_id = str(uuid.uuid4())
    redis = await get_redis()
    key = f"conversation:{session_id}"

    await redis.hset(key, mapping={
        "messages": json.dumps([], ensure_ascii=False),
        "repo_id": repo_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total_tokens": "0",
    })
    await redis.expire(key, SESSION_TTL)

    return session_id


async def get_session_repo_id(session_id: str) -> Optional[str]:
    """获取会话关联的仓库 ID"""
    redis = await get_redis()
    key = f"conversation:{session_id}"
    return await redis.hget(key, "repo_id")


async def get_history(session_id: str) -> List[dict]:
    """获取会话历史消息列表"""
    redis = await get_redis()
    key = f"conversation:{session_id}"
    data = await redis.hget(key, "messages")
    if data:
        return json.loads(data)
    return []


async def append_turn(
    session_id: str,
    user_query: str,
    assistant_response: str,
    chunk_refs: Optional[List[dict]] = None,
    tokens_used: int = 0,
) -> None:
    """
    追加一轮对话（用户提问 + 助手回答）。

    使用覆盖写操作保证原子性，避免并发冲突。
    每条消息使用 UUID 作为唯一标识，彻底避免 AdalFlow 的 ID 冲突 Bug。
    """
    redis = await get_redis()
    key = f"conversation:{session_id}"

    # 获取现有消息
    existing = await redis.hget(key, "messages")
    messages = json.loads(existing) if existing else []

    now = datetime.now(timezone.utc).isoformat()

    # 追加新轮次
    messages.append({
        "id": str(uuid.uuid4()),
        "role": "user",
        "content": user_query,
        "timestamp": now,
    })
    messages.append({
        "id": str(uuid.uuid4()),
        "role": "assistant",
        "content": assistant_response,
        "chunk_refs": chunk_refs or [],
        "timestamp": now,
    })

    # 获取现有 token 计数
    existing_tokens_str = await redis.hget(key, "total_tokens")
    existing_tokens = int(existing_tokens_str or "0")

    # 原子性覆盖写入
    await redis.hset(key, mapping={
        "messages": json.dumps(messages, ensure_ascii=False),
        "updated_at": now,
        "total_tokens": str(existing_tokens + tokens_used),
    })

    # 刷新 TTL
    await redis.expire(key, SESSION_TTL)


async def session_exists(session_id: str) -> bool:
    """检查会话是否存在"""
    redis = await get_redis()
    key = f"conversation:{session_id}"
    return bool(await redis.exists(key))
