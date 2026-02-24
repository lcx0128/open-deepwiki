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

CHAT_SYSTEM_PROMPT = """You are an expert code analysis assistant for the repository "{repo_name}".
Your role is to provide COMPREHENSIVE, ACCURATE answers based on the actual codebase.

CRITICAL RULES:
1. Base ALL answers on the provided code context - cite specific evidence
2. For EVERY function, class, or feature you mention, cite its exact location: `file_path:start_line-end_line`
3. Explain HOW things work, not just WHAT they do - describe algorithms, data flow, call chains
4. If a question involves multiple components, trace the full execution path across modules
5. At the end of your answer, add a structured citations section:
   ---
   **参考代码**:
   - `file_path` (第X-Y行): 功能描述
6. If the provided context is insufficient to fully answer, clearly state what you found AND what information is missing
7. Use Chinese for explanations, keep code identifiers, file paths, and technical terms in English
8. When describing module relationships, explicitly state: "A calls B", "B depends on C", "Data flows from X to Y"
"""

DEEP_RESEARCH_SYSTEM_PROMPT = """You are conducting a thorough multi-turn Deep Research investigation of the repository "{repo_name}".
Your goal is to systematically explore and analyze the codebase to answer the user's question comprehensively.

CRITICAL RULES:
1. Every claim must be backed by specific code evidence with file:line citations
2. Trace execution paths across multiple files and modules
3. Explain HOW the code works, not just WHAT it does
4. Build on previous research iterations - do not repeat information already covered
5. Use Chinese for explanations, keep code identifiers in English
"""

DEEP_RESEARCH_FIRST_PROMPT = """This is iteration 1 (Research Plan) of a Deep Research process.

User question: {question}

Based on the code context provided, create a research plan and provide initial findings:

## Research Plan
- State what specific aspects you will investigate
- List the key files and modules that appear most relevant
- Identify any gaps that need further investigation

## Initial Findings
- Describe what you've found so far with specific code citations (file:line)
- Trace the initial execution flow

## Next Steps
- What specific aspects will you investigate in the next iteration?

MANDATORY: Cite every function/class with its exact file path and line numbers."""

DEEP_RESEARCH_INTERMEDIATE_PROMPT = """This is iteration {iteration} (Research Update {iteration}) of a Deep Research process.

User question: {question}

Previous research context is in the conversation history. Build on it - do NOT repeat what was already covered.

## Research Update {iteration}
- What NEW aspects are you investigating in this iteration?
- New discoveries with specific code citations (file:line)
- How do these findings connect to previous findings?

## Updated Understanding
- Revised or expanded understanding of the system

## Next Steps (if not final)
- What remains to be investigated?

MANDATORY: Cite every new function/class with its exact file path and line numbers."""

DEEP_RESEARCH_FINAL_PROMPT = """This is the FINAL iteration of a Deep Research process (synthesizing all findings).

User question: {question}

Review ALL previous research in the conversation history and synthesize a comprehensive final answer.

## Final Conclusion

### Answer to: {question}
[Comprehensive answer directly addressing the question]

### Complete Execution Flow
[Trace the full execution path from start to finish with file:line citations]

### Key Components
[List all relevant files and their roles with citations]

### Module Relationships
[How components interact with each other]

---
**参考代码 (Complete Citations)**:
[All relevant code locations cited throughout this research]

MANDATORY: This must be a complete, standalone answer that synthesizes ALL research iterations."""


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
    guidelines = await stage1_discovery(fused_query, repo_id, top_k=20)

    # 4. Stage 2: 获取完整代码（选取前 10 个最相关的）
    top_chunk_ids = [g.chunk_id for g in guidelines[:10]]
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
        for g in guidelines[:10]
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
    guidelines = await stage1_discovery(fused_query, repo_id, top_k=20)
    top_chunk_ids = [g.chunk_id for g in guidelines[:10]]
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
        for g in guidelines[:10]
    ]
    yield {"type": "chunk_refs", "refs": chunk_refs}

    # 8. 持久化会话
    await append_turn(session_id, query, full_answer, chunk_refs, 0)

    yield {"type": "done"}


async def handle_deep_research_stream(
    db: AsyncSession,
    repo_id: str,
    query: str,
    messages: List[dict],
    session_id: Optional[str] = None,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
) -> AsyncIterator[dict]:
    """
    Deep Research 流式对话处理。
    支持最多5轮迭代研究：
    - 第1轮: Research Plan (初始发现+研究计划)
    - 第2-4轮: Research Update N (新发现，不重复)
    - 第5轮: Final Conclusion (综合所有轮次)

    事件类型同 handle_chat_stream:
    - {"type": "session_id", "session_id": "..."}
    - {"type": "token", "content": "..."}
    - {"type": "chunk_refs", "refs": [...]}
    - {"type": "deep_research_continue", "iteration": N, "next_iteration": N+1}
    - {"type": "done"}
    - {"type": "error", "error": "..."}
    """
    model = llm_model or settings.DEFAULT_LLM_MODEL
    adapter = create_adapter(llm_provider)

    # 1. 会话管理
    if not session_id:
        session_id = await create_session(repo_id)
    elif not await session_exists(session_id):
        session_id = await create_session(repo_id)

    yield {"type": "session_id", "session_id": session_id}

    # 2. 计算当前迭代次数（assistant 消息数 + 1）
    assistant_count = sum(1 for m in messages if m.get("role") == "assistant")
    iteration = assistant_count + 1
    is_first = (iteration == 1)
    is_final = (iteration >= 5)

    logger.info(f"[DeepResearch] repo={repo_id}, iteration={iteration}, is_final={is_final}")

    # 3. 获取仓库名
    repo_name = await _get_repo_name(db, repo_id)

    # 4. 构建增强查询（用于 RAG 检索）
    history_for_fusion = [
        {"role": m["role"], "content": m["content"]}
        for m in messages[:-1]
    ]
    fused_query = await fuse_query(query, history_for_fusion, llm_provider, llm_model)

    # 5. 双阶段 RAG 检索
    guidelines = await stage1_discovery(fused_query, repo_id, top_k=20)
    top_chunk_ids = [g.chunk_id for g in guidelines[:10]]
    code_contents = await stage2_assembly(top_chunk_ids, repo_id)
    rag_context = "\n\n---\n\n".join(code_contents)

    # 6. 组装系统 prompt
    system_prompt = DEEP_RESEARCH_SYSTEM_PROMPT.format(repo_name=repo_name)

    # 7. 组装用户 prompt（当前轮的指令）
    if is_first:
        user_instruction = DEEP_RESEARCH_FIRST_PROMPT.format(question=query)
    elif is_final:
        user_instruction = DEEP_RESEARCH_FINAL_PROMPT.format(question=query)
    else:
        user_instruction = DEEP_RESEARCH_INTERMEDIATE_PROMPT.format(
            iteration=iteration, question=query
        )

    # 8. Token 预算管理 + 组装完整消息列表
    history_msgs = [m for m in messages[:-1] if m.get("role") in ("user", "assistant")]
    trimmed_history, trimmed_context = apply_token_budget(
        history_msgs, model, system_prompt, rag_context, user_instruction
    )

    messages_to_send = [LLMMessage(role="system", content=system_prompt)]

    # 加入历史对话（裁剪后，防止多轮后超限）
    for m in trimmed_history:
        messages_to_send.append(LLMMessage(role=m.get("role", "user"), content=m.get("content", "")))

    # 加入当前轮的 RAG 上下文 + 指令
    if trimmed_context:
        current_content = (
            f"<code_context>\n{trimmed_context}\n</code_context>\n\n"
            f"{user_instruction}"
        )
    else:
        current_content = user_instruction
        if rag_context:
            logger.warning(f"[DeepResearch] Token 预算不足，移除 RAG 上下文")

    messages_to_send.append(LLMMessage(role="user", content=current_content))

    # 9. 流式生成
    full_answer = ""
    try:
        async for token in adapter.stream_with_rate_limit(
            messages=messages_to_send, model=model, temperature=0.3
        ):
            full_answer += token
            yield {"type": "token", "content": token}
    except Exception as e:
        if "context_length" in str(e).lower():
            logger.warning(f"[DeepResearch] Token 溢出，降级重试")
            fallback_messages = [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_instruction),
            ]
            async for token in adapter.stream_with_rate_limit(
                messages=fallback_messages, model=model, temperature=0.3
            ):
                full_answer += token
                yield {"type": "token", "content": token}
        else:
            yield {"type": "error", "error": str(e)}
            return

    # 10. 返回代码引用
    chunk_refs = [
        {
            "file_path": g.file_path,
            "start_line": g.start_line,
            "end_line": g.end_line,
            "name": g.name,
        }
        for g in guidelines[:10]
    ]
    yield {"type": "chunk_refs", "refs": chunk_refs}

    # 11. 持久化
    await append_turn(session_id, query, full_answer, chunk_refs, 0)

    # 12. 非最终轮提示前端继续
    if not is_final:
        yield {"type": "deep_research_continue", "iteration": iteration, "next_iteration": iteration + 1}

    yield {"type": "done"}
