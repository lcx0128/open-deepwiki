# app/services/chat_service.py
import logging
from typing import AsyncIterator, Optional, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.query_fusion import fuse_query
from app.services.two_stage_retriever import stage1_discovery, stage2_assembly
from app.services.conversation_memory import create_session, get_history, append_turn, session_exists
from app.services.token_budget import apply_token_budget, estimate_tokens
from app.services.llm.factory import create_adapter
from app.schemas.llm import LLMMessage
from app.schemas.mcp_types import CodeGuideline
from app.config import settings

logger = logging.getLogger(__name__)

CHAT_SYSTEM_PROMPT = """You are a code expert assistant for the repository "{repo_name}".
Answer questions based ONLY on the provided code context.

Rules:
1. Base your answers on the actual code provided, not general knowledge
2. Include specific file paths and line numbers in your references
3. At the end of your answer, ALWAYS add source citations in this format:
   > 参考代码: file_path (第X-Y行)
4. If the provided code context does not contain enough information, say so clearly
5. Use Chinese for explanations, keep code identifiers in English
"""


async def _get_repo_name(db: AsyncSession, repo_id: str) -> str:
    """获取仓库名称，用于 system prompt"""
    try:
        from app.models.repository import Repository
        result = await db.execute(
            select(Repository).where(Repository.id == repo_id)
        )
        repo = result.scalar_one_or_none()
        if repo:
            return repo.name or repo_id
    except Exception:
        pass
    return repo_id


async def handle_chat(
    db: AsyncSession,
    repo_id: str,
    query: str,
    session_id: Optional[str] = None,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
) -> dict:
    """
    非流式对话处理主流程。

    流程：
    1. 会话管理（创建或复用）
    2. 查询融合（多轮代词消解）
    3. Stage 1: ChromaDB 语义检索（轻量导引）
    4. Stage 2: 获取完整代码片段
    5. Token 预算管理
    6. 组装 Prompt 并调用 LLM
    7. 上下文降级兜底（context_length_exceeded）
    8. 持久化会话历史
    """
    model = llm_model or settings.DEFAULT_LLM_MODEL
    adapter = create_adapter(llm_provider)

    # 1. 会话管理
    if not session_id:
        session_id = await create_session(repo_id)
    elif not await session_exists(session_id):
        # 会话已过期（Redis TTL 到期），重新创建
        session_id = await create_session(repo_id)

    history = await get_history(session_id)

    # 2. 查询融合
    fused_query = await fuse_query(query, history, llm_provider, llm_model)

    # 3. Stage 1: 检索导引
    guidelines = await stage1_discovery(fused_query, repo_id, top_k=10)

    # 4. Stage 2: 获取完整代码（选取前 5 个最相关的）
    top_chunk_ids = [g.chunk_id for g in guidelines[:5]]
    code_contents = await stage2_assembly(top_chunk_ids, repo_id)
    rag_context = "\n\n---\n\n".join(code_contents)

    # 5. Token 预算管理
    repo_name = await _get_repo_name(db, repo_id)
    system_prompt = CHAT_SYSTEM_PROMPT.format(repo_name=repo_name)
    trimmed_history, trimmed_context = apply_token_budget(
        history, model, system_prompt, rag_context, query
    )

    # 6. 组装最终 Prompt
    messages = [LLMMessage(role="system", content=system_prompt)]

    for msg in trimmed_history:
        role = msg.get("role", "user")
        if role in ("user", "assistant"):
            messages.append(LLMMessage(role=role, content=msg.get("content", "")))

    if trimmed_context:
        messages.append(LLMMessage(
            role="user",
            content=f"<code_context>\n{trimmed_context}\n</code_context>\n\n{query}"
        ))
    else:
        messages.append(LLMMessage(role="user", content=query))

    # 7. 调用 LLM，带上下文降级兜底
    try:
        response = await adapter.generate_with_rate_limit(
            messages=messages, model=model, temperature=0.3
        )
        answer = response.content
    except Exception as e:
        if "context_length" in str(e).lower() or "context length" in str(e).lower():
            logger.warning(f"[ChatService] Token 溢出，降级：移除 RAG 上下文重试")
            fallback_messages = [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=query),
            ]
            response = await adapter.generate_with_rate_limit(
                messages=fallback_messages, model=model, temperature=0.3
            )
            answer = response.content + "\n\n> 注意：由于上下文限制，本回答未参考代码上下文。"
        else:
            raise

    # 8. 构建引用列表
    chunk_refs = [
        {
            "file_path": g.file_path,
            "start_line": g.start_line,
            "end_line": g.end_line,
            "name": g.name,
        }
        for g in guidelines[:5]
    ]

    # 9. 持久化会话
    tokens_used = 0
    if response.usage:
        tokens_used = response.usage.get("total_tokens", 0)

    await append_turn(session_id, query, answer, chunk_refs, tokens_used)

    return {
        "session_id": session_id,
        "answer": answer,
        "chunk_refs": chunk_refs,
        "usage": response.usage,
    }


async def handle_chat_stream(
    db: AsyncSession,
    repo_id: str,
    query: str,
    session_id: Optional[str] = None,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
) -> AsyncIterator[dict]:
    """
    流式对话处理主流程，逐 token 生成 SSE 事件。

    事件类型:
    - {"type": "session_id", "session_id": "..."} -- 首条，返回 session_id
    - {"type": "token", "content": "..."} -- 每个生成的 token
    - {"type": "chunk_refs", "refs": [...]} -- 代码引用列表
    - {"type": "done"} -- 完成标志
    - {"type": "error", "error": "..."} -- 错误信息
    """
    model = llm_model or settings.DEFAULT_LLM_MODEL
    adapter = create_adapter(llm_provider)

    # 1. 会话管理
    if not session_id:
        session_id = await create_session(repo_id)
    elif not await session_exists(session_id):
        session_id = await create_session(repo_id)

    # 返回 session_id（客户端首条消息）
    yield {"type": "session_id", "session_id": session_id}

    history = await get_history(session_id)

    # 2. 查询融合
    fused_query = await fuse_query(query, history, llm_provider, llm_model)

    # 3. Stage 1 + Stage 2 检索
    guidelines = await stage1_discovery(fused_query, repo_id, top_k=10)
    top_chunk_ids = [g.chunk_id for g in guidelines[:5]]
    code_contents = await stage2_assembly(top_chunk_ids, repo_id)
    rag_context = "\n\n---\n\n".join(code_contents)

    # 4. Token 预算管理
    repo_name = await _get_repo_name(db, repo_id)
    system_prompt = CHAT_SYSTEM_PROMPT.format(repo_name=repo_name)
    trimmed_history, trimmed_context = apply_token_budget(
        history, model, system_prompt, rag_context, query
    )

    # 5. 组装 Prompt
    messages = [LLMMessage(role="system", content=system_prompt)]
    for msg in trimmed_history:
        role = msg.get("role", "user")
        if role in ("user", "assistant"):
            messages.append(LLMMessage(role=role, content=msg.get("content", "")))

    if trimmed_context:
        messages.append(LLMMessage(
            role="user",
            content=f"<code_context>\n{trimmed_context}\n</code_context>\n\n{query}"
        ))
    else:
        messages.append(LLMMessage(role="user", content=query))

    # 6. 流式生成
    full_answer = ""
    try:
        async for token in adapter.stream_with_rate_limit(
            messages=messages, model=model, temperature=0.3
        ):
            full_answer += token
            yield {"type": "token", "content": token}
    except Exception as e:
        if "context_length" in str(e).lower():
            logger.warning(f"[ChatService] 流式 Token 溢出，降级重试")
            fallback_messages = [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=query),
            ]
            async for token in adapter.stream_with_rate_limit(
                messages=fallback_messages, model=model, temperature=0.3
            ):
                full_answer += token
                yield {"type": "token", "content": token}
            suffix = "\n\n> 注意：由于上下文限制，本回答未参考代码上下文。"
            full_answer += suffix
            yield {"type": "token", "content": suffix}
        else:
            yield {"type": "error", "error": str(e)}
            return

    # 7. 返回代码引用
    chunk_refs = [
        {
            "file_path": g.file_path,
            "start_line": g.start_line,
            "end_line": g.end_line,
            "name": g.name,
        }
        for g in guidelines[:5]
    ]
    yield {"type": "chunk_refs", "refs": chunk_refs}

    # 8. 持久化会话
    await append_turn(session_id, query, full_answer, chunk_refs, 0)

    yield {"type": "done"}
