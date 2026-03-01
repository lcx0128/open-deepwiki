"""
运行时系统配置持久化模块。

使用 data/system_config.json 存储配置覆盖项，优先级高于 .env 文件。
这样用户可以通过 API 修改配置而无需重启服务。
"""
import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path("data/system_config.json")
_config_lock = threading.Lock()

# 所有可管理的配置键
MANAGED_KEYS = [
    "DEFAULT_LLM_PROVIDER",
    "DEFAULT_LLM_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "DASHSCOPE_API_KEY",
    "GOOGLE_API_KEY",
    "CUSTOM_LLM_BASE_URL",
    "CUSTOM_LLM_API_KEY",
    "EMBEDDING_API_KEY",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_MODEL",
    "WIKI_LANGUAGE",
]

def load_system_config() -> dict:
    """加载运行时配置覆盖（从 data/system_config.json）。"""
    if _CONFIG_PATH.exists():
        try:
            return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[SystemConfig] 读取配置文件失败，使用空配置: {e}")
    return {}

def save_system_config(config: dict) -> None:
    """保存运行时配置覆盖到 data/system_config.json。"""
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    logger.info("[SystemConfig] 配置已保存")

def get_effective_config() -> dict[str, Any]:
    """
    获取当前生效配置（system_config.json 覆盖 > .env / 环境变量）。
    返回所有 MANAGED_KEYS 对应的值。
    """
    from app.config import settings
    overrides = load_system_config()

    defaults = {
        "DEFAULT_LLM_PROVIDER": settings.DEFAULT_LLM_PROVIDER,
        "DEFAULT_LLM_MODEL": settings.DEFAULT_LLM_MODEL,
        "OPENAI_API_KEY": settings.OPENAI_API_KEY,
        "OPENAI_BASE_URL": settings.OPENAI_BASE_URL,
        "DASHSCOPE_API_KEY": settings.DASHSCOPE_API_KEY,
        "GOOGLE_API_KEY": settings.GOOGLE_API_KEY,
        "CUSTOM_LLM_BASE_URL": settings.CUSTOM_LLM_BASE_URL,
        "CUSTOM_LLM_API_KEY": settings.CUSTOM_LLM_API_KEY,
        "EMBEDDING_API_KEY": settings.EMBEDDING_API_KEY,
        "EMBEDDING_BASE_URL": settings.EMBEDDING_BASE_URL,
        "EMBEDDING_MODEL": settings.EMBEDDING_MODEL,
        "WIKI_LANGUAGE": settings.WIKI_LANGUAGE,
    }

    # overrides 中存在的键优先
    for key in MANAGED_KEYS:
        if key in overrides and overrides[key] is not None:
            defaults[key] = overrides[key]

    return defaults

def update_system_config(updates: dict) -> dict:
    """
    更新配置（仅更新提供的键），保存并返回更新后的完整覆盖配置。
    如果值以 "****" 开头（脱敏格式），跳过该键不更新。
    如果值为空字符串，则从覆盖配置中移除该键（回退到默认值）。
    使用线程锁防止并发写入导致的数据丢失。
    """
    with _config_lock:
        current = load_system_config()

        for key, value in updates.items():
            if key not in MANAGED_KEYS:
                continue
            if isinstance(value, str) and value.startswith("****"):
                # 脱敏格式，用户未修改，跳过
                continue
            if value == "" or value is None:
                # 空值 = 移除覆盖，回退到 .env
                current.pop(key, None)
            else:
                current[key] = value

        save_system_config(current)
        return current
