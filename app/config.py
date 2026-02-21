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

    # Embedding
    EMBEDDING_MODEL: str = "text-embedding-ada-002"
    EMBEDDING_DIM: int = 1536

    # 并发
    MAX_CONCURRENT_LLM_CALLS: int = 10

    # MCP
    MCP_AUTH_TOKEN: Optional[str] = None

    # Wiki 生成语言（生成的所有 Wiki 内容强制使用该语言）
    # 示例：Chinese / English / Japanese / French / German
    WIKI_LANGUAGE: str = "Chinese"

    # 应用
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    REPOS_BASE_DIR: str = "./repos"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
