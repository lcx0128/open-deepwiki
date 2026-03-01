import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import init_db, engine
from app.core.redis_client import init_redis, close_redis
from app.config import settings

# 配置日志
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动时初始化资源，关闭时释放"""
    # === 启动阶段 ===
    logger.info("[Lifespan] 正在初始化数据库...")
    await init_db()
    logger.info("[Lifespan] 正在初始化 Redis 连接池...")
    await init_redis()
    logger.info("[Lifespan] 数据库和 Redis 连接池初始化完成")
    yield
    # === 关闭阶段 ===
    logger.info("[Lifespan] 正在关闭 Redis 连接...")
    await close_redis()
    logger.info("[Lifespan] 正在关闭数据库连接池...")
    await engine.dispose()
    logger.info("[Lifespan] 所有连接已优雅关闭")


app = FastAPI(
    title="Open-DeepWiki API",
    description="企业级代码知识库与 AI 智能体系统",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 配置
# 注意：allow_credentials=True 与 allow_origins=["*"] 不兼容（浏览器会拒绝）
# 生产环境应将 allow_origins 改为具体的前端域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 确保 ORM 模型注册到 SQLAlchemy（必须在 init_db 之前导入）
from app.models.repo_index import RepoIndex  # noqa: F401

# 注册路由
from app.api.repositories import router as repositories_router
from app.api.tasks import router as tasks_router
from app.api.wiki import router as wiki_router
from app.api.chat import router as chat_router
from app.api.system import router as system_router

app.include_router(repositories_router)
app.include_router(tasks_router)
app.include_router(wiki_router)
app.include_router(chat_router)
app.include_router(system_router)


@app.get("/health", tags=["health"])
async def health_check():
    """健康检查端点"""
    return {"status": "ok", "version": "1.0.0"}
