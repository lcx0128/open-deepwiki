import logging
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.config import settings
from app.core.auth import create_session_token, delete_session_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str


@router.get("/status", summary="获取鉴权状态")
async def get_auth_status():
    """返回鉴权是否启用，前端据此决定是否跳转登录页。始终公开访问。"""
    return {"enabled": settings.AUTH_ENABLED}


@router.post("/login", summary="登录")
async def login(body: LoginRequest):
    """验证口令，成功后颁发会话 token（存储在 Redis，有效期由 AUTH_SESSION_EXPIRE_HOURS 控制）。"""
    if not settings.AUTH_ENABLED:
        return {"token": None, "message": "鉴权未启用"}

    if not settings.AUTH_PASSWORD:
        raise HTTPException(
            status_code=503,
            detail="AUTH_PASSWORD 未配置，请在 .env 中设置登录口令",
        )

    if body.password != settings.AUTH_PASSWORD:
        raise HTTPException(status_code=401, detail="口令错误")

    token = await create_session_token()
    logger.info("[Auth] 登录成功，颁发新会话 token")
    return {"token": token}


@router.post("/logout", summary="登出")
async def logout(request: Request):
    """使当前会话 token 立即失效。"""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        await delete_session_token(token)
    return {"message": "已登出"}
