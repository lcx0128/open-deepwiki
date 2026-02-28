"""
文档解析器：处理 Markdown、RST、TXT 和配置文件，生成 ChunkNode 供向量化使用。
"""
import json
import logging
import re
from pathlib import Path
from typing import List

from app.schemas.chunk_node import ChunkNode

logger = logging.getLogger(__name__)

# 最大内容长度（字符数）
_MAX_SECTION_CHARS = 8000
_MAX_CONFIG_CHARS = 5000
_MAX_PACKAGE_JSON_CHARS = 3000


def parse_doc_file(file_path: str, source_code: str, language: str) -> List[ChunkNode]:
    """
    解析文档文件（.md/.rst/.txt），返回 ChunkNode 列表。
    - markdown: 按标题（H1/H2/H3）切分
    - restructuredtext: 整个文件作为单个 chunk
    - text: 按段落切分
    """
    if not source_code.strip():
        return []

    if language == "markdown":
        return _parse_markdown(file_path, source_code)
    elif language == "restructuredtext":
        return _parse_as_single_chunk(file_path, source_code, "restructuredtext")
    else:
        return _parse_text_paragraphs(file_path, source_code)


def parse_config_file(file_path: str, source_code: str) -> List[ChunkNode]:
    """
    解析配置文件，返回 ChunkNode 列表。
    package.json 提取关键字段；其他文件直接作为单 chunk。
    """
    if not source_code.strip():
        return []

    filename = Path(file_path).name
    if filename == "package.json":
        return _parse_package_json(file_path, source_code)
    else:
        lang = "yaml" if filename.endswith((".yml", ".yaml")) else "toml" if filename.endswith(".toml") else "text"
        return _parse_as_single_chunk(file_path, source_code, lang, max_chars=_MAX_CONFIG_CHARS)


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────

def _count_line(source: str, byte_pos: int) -> int:
    """将字符偏移量转换为 1-indexed 行号"""
    return source[:byte_pos].count('\n') + 1


def _parse_markdown(file_path: str, source_code: str) -> List[ChunkNode]:
    """按 H1/H2/H3 标题切分 Markdown 文档"""
    heading_re = re.compile(r'^(#{1,3})\s+(.+)$', re.MULTILINE)
    matches = list(heading_re.finditer(source_code))

    chunks: List[ChunkNode] = []

    if not matches:
        # 无标题：整个文件作为一个 chunk
        name = Path(file_path).stem
        content = source_code[:_MAX_SECTION_CHARS]
        total_lines = source_code.count('\n') + 1
        chunks.append(ChunkNode(
            file_path=file_path,
            node_type="document_section",
            name=name,
            start_line=1,
            end_line=total_lines,
            content=content,
            language="markdown",
        ))
        return chunks

    for i, match in enumerate(matches):
        heading_text = match.group(2).strip()
        start_pos = match.start()
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(source_code)
        section_content = source_code[start_pos:end_pos].strip()

        if len(section_content) < 50:
            continue

        start_line = _count_line(source_code, start_pos)
        end_line = _count_line(source_code, end_pos)

        # 内容过长时按段落拆分（保留重叠）
        if len(section_content) > _MAX_SECTION_CHARS:
            sub_chunks = _split_by_paragraphs(
                file_path, section_content, heading_text,
                start_line, end_line, "markdown"
            )
            chunks.extend(sub_chunks)
        else:
            chunks.append(ChunkNode(
                file_path=file_path,
                node_type="document_section",
                name=heading_text[:100],
                start_line=start_line,
                end_line=end_line,
                content=section_content,
                language="markdown",
            ))

    return chunks


def _parse_text_paragraphs(file_path: str, source_code: str) -> List[ChunkNode]:
    """按双换行切分纯文本为段落"""
    paragraphs = re.split(r'\n{2,}', source_code)
    chunks: List[ChunkNode] = []
    current_line = 1

    for para in paragraphs:
        para_stripped = para.strip()
        line_count = para.count('\n') + 1

        if len(para_stripped) >= 100:
            chunks.append(ChunkNode(
                file_path=file_path,
                node_type="document_section",
                name=para_stripped[:60],
                start_line=current_line,
                end_line=current_line + line_count - 1,
                content=para_stripped[:_MAX_SECTION_CHARS],
                language="text",
            ))

        current_line += line_count + 1  # +1 for the blank line separator

    return chunks


def _parse_as_single_chunk(
    file_path: str, source_code: str, language: str, max_chars: int = _MAX_SECTION_CHARS
) -> List[ChunkNode]:
    """将整个文件作为单个 document_section chunk"""
    name = Path(file_path).name
    total_lines = source_code.count('\n') + 1
    return [ChunkNode(
        file_path=file_path,
        node_type="document_section",
        name=name,
        start_line=1,
        end_line=total_lines,
        content=source_code[:max_chars],
        language=language,
    )]


def _parse_package_json(file_path: str, source_code: str) -> List[ChunkNode]:
    """解析 package.json，提取关键字段生成可读文本 chunk"""
    total_lines = source_code.count('\n') + 1
    try:
        data = json.loads(source_code)
        lines = []
        if data.get("name"):
            lines.append(f"Package: {data['name']}")
        if data.get("version"):
            lines.append(f"Version: {data['version']}")
        if data.get("description"):
            lines.append(f"Description: {data['description']}")
        if data.get("scripts"):
            scripts_str = ", ".join(f"{k}: {v}" for k, v in list(data["scripts"].items())[:20])
            lines.append(f"Scripts: {scripts_str}")
        if data.get("dependencies"):
            deps = list(data["dependencies"].keys())[:30]
            lines.append(f"Dependencies: {', '.join(deps)}")
        content = "\n".join(lines)[:_MAX_PACKAGE_JSON_CHARS]
    except Exception:
        content = source_code[:_MAX_PACKAGE_JSON_CHARS]

    return [ChunkNode(
        file_path=file_path,
        node_type="config_file",
        name="package.json",
        start_line=1,
        end_line=total_lines,
        content=content,
        language="json",
    )]


def _split_by_paragraphs(
    file_path: str, content: str, base_name: str,
    base_start_line: int, base_end_line: int, language: str,
    overlap: int = 200,
) -> List[ChunkNode]:
    """将超长 section 按段落边界切分为多个子 chunk，保留 200 字符重叠"""
    paragraphs = re.split(r'\n{2,}', content)
    chunks: List[ChunkNode] = []
    current = ""
    part = 1

    for para in paragraphs:
        if len(current) + len(para) > _MAX_SECTION_CHARS:
            if current.strip():
                chunks.append(ChunkNode(
                    file_path=file_path,
                    node_type="document_section",
                    name=f"{base_name[:80]} (part {part})",
                    start_line=base_start_line,
                    end_line=base_end_line,
                    content=current.strip(),
                    language=language,
                ))
                part += 1
                # 保留末尾重叠
                current = current[-overlap:] + "\n\n" + para
            else:
                current = para
        else:
            current = current + "\n\n" + para if current else para

    if current.strip():
        chunks.append(ChunkNode(
            file_path=file_path,
            node_type="document_section",
            name=f"{base_name[:80]} (part {part})" if part > 1 else base_name[:100],
            start_line=base_start_line,
            end_line=base_end_line,
            content=current.strip(),
            language=language,
        ))

    return chunks
