# app/services/query_fusion.py
import logging
from typing import List, Optional

from app.schemas.llm import LLMMessage
from app.services.llm.factory import create_adapter

logger = logging.getLogger(__name__)

QUERY_REWRITE_PROMPT = """You are a query rewriter for a code search system.

Given the conversation history and the latest question, rewrite the question
into a standalone, self-contained query that can be used for semantic search
over a code repository.

Rules:
1. Resolve all pronouns and references (e.g., "this function" -> actual function name)
2. Preserve technical terms and code identifiers exactly
3. Return ONLY the rewritten query, nothing else
4. If the question is already self-contained, return it as-is

Conversation history:
{history}

Latest question: {question}

Rewritten query:"""


async def fuse_query(
    question: str,
    chat_history: List[dict],
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
) -> str:
    """
    将多轮对话上下文与当前问题融合为独立查询。

    当 chat_history 为空时，直接返回原始问题（节省 LLM 调用）。
    当 chat_history 包含内容时，使用轻量级 LLM 重写以解决代词指代问题。
    """
    if not chat_history:
        return question

    # 格式化历史（最多取最近 6 轮对话，截断过长内容）
    history_text = ""
    for msg in chat_history[-6:]:
        role = "User" if msg["role"] == "user" else "Assistant"
        content = msg.get("content", "")[:200]  # 截断过长内容
        history_text += f"{role}: {content}\n"

    try:
        adapter = create_adapter(llm_provider)
        # 使用轻量级模型做查询重写，节省 Token
        model = llm_model or "gpt-4o-mini"
        response = await adapter.generate_with_rate_limit(
            messages=[
                LLMMessage(role="user", content=QUERY_REWRITE_PROMPT.format(
                    history=history_text, question=question
                )),
            ],
            model=model,
            temperature=0.1,
            max_tokens=200,
        )
        rewritten = response.content.strip()
        logger.debug(f"[QueryFusion] 查询重写: '{question}' -> '{rewritten}'")
        return rewritten if rewritten else question
    except Exception as e:
        logger.warning(f"[QueryFusion] 查询重写失败，使用原始问题: {e}")
        return question
