from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # 数据库
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/deepwiki.db"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # LLM 配置
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: Optional[str] = "https://api.openai.com/v1"
    DASHSCOPE_API_KEY: Optional[str] = None
    GOOGLE_API_KEY: Optional[str] = None
    CUSTOM_LLM_BASE_URL: Optional[str] = None
    CUSTOM_LLM_API_KEY: Optional[str] = None
    DEFAULT_LLM_PROVIDER: str = "openai"
    DEFAULT_LLM_MODEL: str = "gpt-4o"

    # ChromaDB
    CHROMADB_PATH: str = "./data/chromadb"

    # Embedding（可独立于 LLM API 配置；留空则回退到 OPENAI_API_KEY / OPENAI_BASE_URL）
    EMBEDDING_API_KEY: Optional[str] = None
    EMBEDDING_BASE_URL: Optional[str] = None
    EMBEDDING_MODEL: str = "text-embedding-ada-002"
    EMBEDDING_DIM: int = 1536

    # 并发
    MAX_CONCURRENT_LLM_CALLS: int = 10
    WIKI_PAGE_CONCURRENCY: int = 3

    # MCP
    MCP_AUTH_TOKEN: Optional[str] = None

    # 访问鉴权（部署到公网时开启，防止未授权提交）
    AUTH_ENABLED: bool = False
    AUTH_PASSWORD: Optional[str] = None
    AUTH_SESSION_EXPIRE_HOURS: int = 168  # 默认 7 天

    # Wiki 生成语言（生成的所有 Wiki 内容强制使用该语言）
    # 示例：Chinese / English / Japanese / French / German
    WIKI_LANGUAGE: str = "Chinese"

    # 应用
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    REPOS_BASE_DIR: str = "./repos"

    # 文件日志
    ENABLE_FILE_LOGGING: bool = False        # 是否将日志写入本地文件
    LOG_DIR: str = "logs"                    # 日志目录
    LOG_MAX_SIZE_MB: int = 50               # 单个日志文件最大体积（MB），超出后轮转
    LOG_BACKUP_COUNT: int = 5               # 保留的历史日志份数（.1/.2/...）

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
