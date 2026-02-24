import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Token 溢出错误关键词
_TOKEN_OVERFLOW_KEYWORDS = [
    "context_length_exceeded",
    "maximum context length",
    "token limit",
    "max_tokens",
    "content too large",
    "request too large",
    "too many tokens",
    "input too long",
]


def is_token_overflow(error: Exception) -> bool:
    """判断异常是否为 Token 溢出"""
    error_str = str(error).lower()
    return any(kw in error_str for kw in _TOKEN_OVERFLOW_KEYWORDS)


async def generate_with_degradation(
    adapter,
    model: str,
    page_data: dict,
    section_title: str,
    repo_name: str,
    code_context: str,
    page_content_prompt_template: str,
    mermaid_constraint_prompt: str,
) -> str:
    """
    多级降级生成策略。

    降级层级：
    1. 截断 code_context 到 50%
    2. 截断 code_context 到 25%
    3. 移除 code_context，仅用页面标题和文件列表
    """
    from app.schemas.llm import LLMMessage

    async def _try_generate(ctx: str) -> str:
        from app.services.mermaid_validator import validate_and_fix_mermaid
        messages = [
            LLMMessage(role="system", content=mermaid_constraint_prompt),
            LLMMessage(role="user", content=page_content_prompt_template.format(
                page_title=page_data["title"],
                section_title=section_title,
                repo_name=repo_name,
                code_context=ctx,
                mermaid_constraints=mermaid_constraint_prompt,
            )),
        ]
        response = await adapter.generate_with_rate_limit(
            messages=messages, model=model, temperature=0.5,
        )
        content = response.content
        content = await validate_and_fix_mermaid(adapter, model, content)
        return content

    # 第一级降级：截断 50%
    truncated = code_context[:len(code_context) // 2]
    logger.warning("[TokenDegradation] 第一级降级：截断 code_context 到 50%")
    try:
        return await _try_generate(truncated)
    except Exception as e:
        if not is_token_overflow(e):
            raise

    # 第二级降级：截断 25%
    truncated = code_context[:len(code_context) // 4]
    logger.warning("[TokenDegradation] 第二级降级：截断 code_context 到 25%")
    try:
        return await _try_generate(truncated)
    except Exception as e:
        if not is_token_overflow(e):
            raise

    # 第三级降级：无代码上下文
    minimal_context = f"Files: {', '.join(page_data.get('relevant_files', []))}"
    logger.warning("[TokenDegradation] 第三级降级：移除 code_context")
    return await _try_generate(minimal_context)
