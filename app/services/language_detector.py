from pathlib import Path
from typing import Optional
import tree_sitter_python as ts_python
import tree_sitter_javascript as ts_javascript
import tree_sitter_typescript as ts_typescript
import tree_sitter_go as ts_go
import tree_sitter_rust as ts_rust
import tree_sitter_java as ts_java
from tree_sitter import Language, Parser

# 扩展名到语言的映射
EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
}

# 语言到 Tree-sitter 语法库的映射
LANGUAGE_MODULES = {
    "python": ts_python,
    "javascript": ts_javascript,
    "typescript": ts_typescript,
    "go": ts_go,
    "rust": ts_rust,
    "java": ts_java,
}

# 需要跳过的目录和文件模式
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".nuxt", "target", "vendor",
    ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
}

SKIP_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "Pipfile.lock", "poetry.lock", "go.sum",
}

# 单文件大小限制
MAX_FILE_SIZE_BYTES = 1024 * 1024  # 1MB


def detect_language(file_path: str) -> Optional[str]:
    """根据文件扩展名检测编程语言"""
    suffix = Path(file_path).suffix.lower()
    return EXTENSION_MAP.get(suffix)


def get_parser(language: str) -> Parser:
    """获取指定语言的 Tree-sitter Parser"""
    module = LANGUAGE_MODULES.get(language)
    if not module:
        raise ValueError(f"不支持的语言: {language}")

    parser = Parser()
    # tree-sitter-typescript 的顶层 API 不同于其他包：
    # 它暴露的是 language_typescript() 和 language_tsx()，而非 language()
    if language == "typescript":
        lang = Language(module.language_typescript())
    else:
        lang = Language(module.language())
    parser.language = lang
    return parser


def should_skip(file_path: str) -> bool:
    """判断文件是否应该被跳过"""
    path = Path(file_path)

    # 跳过特定目录
    for part in path.parts:
        if part in SKIP_DIRS:
            return True

    # 跳过特定文件
    if path.name in SKIP_FILES:
        return True

    # 跳过不支持的扩展名
    if path.suffix.lower() not in EXTENSION_MAP:
        return True

    # 跳过过大文件
    try:
        if path.stat().st_size > MAX_FILE_SIZE_BYTES:
            return True
    except OSError:
        return True

    return False
