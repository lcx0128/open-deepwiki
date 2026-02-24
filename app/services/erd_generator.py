import re
import logging
from typing import List, Optional

from app.schemas.chunk_node import ChunkNode
from app.services.llm.adapter import BaseLLMAdapter
from app.schemas.llm import LLMMessage

logger = logging.getLogger(__name__)

ERD_PROMPT_TEMPLATE = """Based on the following ORM model definitions, generate a Mermaid erDiagram.

Models:
{models_text}

Rules:
1. Use `erDiagram` keyword
2. Cardinality notation:
   - `||--o{{` = one-to-many (escape curly braces in Python f-strings)
   - `||--||` = one-to-one
   - `}}o--o{{` = many-to-many
3. List ALL fields with their types
4. Mark primary keys with PK
5. Mark foreign keys with FK
6. Return ONLY the Mermaid erDiagram code, no other text, no ```mermaid wrapper
"""


def format_orm_models_for_prompt(orm_chunks: List[ChunkNode]) -> str:
    """将 ORM ChunkNode 列表格式化为 Prompt 文本"""
    parts = []
    for chunk in orm_chunks:
        if not chunk.is_orm_model or not chunk.orm_fields:
            continue
        lines = [f"Model: {chunk.name} (file: {chunk.file_path})"]
        for field in chunk.orm_fields:
            constraints = []
            if field.get("primary_key"):
                constraints.append("PK")
            if field.get("foreign_key"):
                constraints.append(f"FK -> {field['foreign_key']}")
            if not field.get("nullable", True):
                constraints.append("NOT NULL")
            constraint_str = f" [{', '.join(constraints)}]" if constraints else ""
            lines.append(f"  - {field['name']}: {field['type']}{constraint_str}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


async def generate_erd(
    adapter: BaseLLMAdapter,
    model: str,
    orm_chunks: List[ChunkNode],
) -> str:
    """
    生成 ERD Mermaid 图。
    若没有 ORM 模型，返回空字符串。
    """
    models_text = format_orm_models_for_prompt(orm_chunks)
    if not models_text:
        return ""

    logger.info(f"[ERDGenerator] 生成 ERD，ORM 模型数: {len([c for c in orm_chunks if c.is_orm_model])}")

    try:
        response = await adapter.generate_with_rate_limit(
            messages=[
                LLMMessage(
                    role="user",
                    content=ERD_PROMPT_TEMPLATE.format(models_text=models_text),
                ),
            ],
            model=model,
            temperature=0.1,
        )
        erd_code = response.content.strip()
        # 清理可能的 wrapper
        erd_code = re.sub(r'^```mermaid\s*\n?', '', erd_code)
        erd_code = re.sub(r'\n?```\s*$', '', erd_code)
        return f"```mermaid\n{erd_code}\n```"
    except Exception as e:
        logger.error(f"[ERDGenerator] ERD 生成失败: {e}")
        return ""
