import secrets
import logging
from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

AUTH_TOKEN_PREFIX = "auth:session:"


async def create_session_token() -> str:
    """生成会话 token 并存入 Redis，返回 token 字符串。"""
    from app.core.redis_client import get_redis
    from app.config import settings

    token = secrets.token_urlsafe(32)
    redis = await get_redis()
    expire_seconds = settings.AUTH_SESSION_EXPIRE_HOURS * 3600
    await redis.setex(f"{AUTH_TOKEN_PREFIX}{token}", expire_seconds, "1")
    return token


async def delete_session_token(token: str) -> None:
    """删除会话 token（登出）。"""
    from app.core.redis_client import get_redis

    redis = await get_redis()
    await redis.delete(f"{AUTH_TOKEN_PREFIX}{token}")


async def verify_session_token(token: str) -> bool:
    """检查 token 在 Redis 中是否存在且有效。"""
    from app.core.redis_client import get_redis

    redis = await get_redis()
    return bool(await redis.exists(f"{AUTH_TOKEN_PREFIX}{token}"))


async def require_auth(request: Request) -> None:
    """FastAPI 依赖项：验证请求携带有效会话 token。

    AUTH_ENABLED=False 时直接放行，无任何开销。
    Token 来源（按优先级）：
      1. Authorization: Bearer <token> 请求头（普通 API 请求）
      2. ?token=<token> 查询参数（EventSource SSE，不支持自定义 header）
    """
    from app.config import settings

    if not settings.AUTH_ENABLED:
        return

    token: str | None = None

    # 优先从 Bearer header 获取
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()

    # SSE 降级：从 query param 获取
    if not token:
        token = request.query_params.get("token")

    if not token:
        raise HTTPException(status_code=401, detail="未授权，请登录")

    if not await verify_session_token(token):
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
