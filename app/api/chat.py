# app/api/chat.py
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import handle_chat, handle_chat_stream, handle_deep_research_stream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post(
    "",
    summary="发起对话（非流式 / Deep Research 流式）",
)
async def chat(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    基于 RAG 的多轮对话接口。
    - deep_research=False（默认）：非流式，返回 ChatResponse JSON
    - deep_research=True：流式 SSE，实现多轮迭代研究
    """
    if request.deep_research:
        messages = request.messages or [{"role": "user", "content": request.query}]

        async def dr_event_generator():
            try:
                async for event in handle_deep_research_stream(
                    db=db,
                    repo_id=request.repo_id,
                    query=request.query,
                    messages=messages,
                    session_id=request.session_id,
                    llm_provider=request.llm_provider,
                    llm_model=request.llm_model,
                ):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as e:
                logger.exception(f"[ChatAPI] Deep Research 失败: {e}")
                error_event = {"type": "error", "error": str(e)}
                yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            dr_event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # 普通模式
    try:
        result = await handle_chat(
            db=db,
            repo_id=request.repo_id,
            query=request.query,
            session_id=request.session_id,
            llm_provider=request.llm_provider,
            llm_model=request.llm_model,
        )
        return ChatResponse(**result)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"[ChatAPI] 对话处理失败: {e}")
        raise HTTPException(status_code=500, detail=f"对话处理失败: {str(e)}")


@router.get(
    "/stream",
    summary="发起对话（SSE 流式）",
)
async def chat_stream(
    repo_id: str = Query(..., description="仓库 ID"),
    query: str = Query(..., description="用户问题"),
    session_id: Optional[str] = Query(None, description="会话 ID，首次为空"),
    llm_provider: Optional[str] = Query(None, description="LLM 供应商"),
    llm_model: Optional[str] = Query(None, description="LLM 模型"),
    deep_research: bool = Query(False, description="是否启用 Deep Research 模式"),
    db: AsyncSession = Depends(get_db),
):
    """
    基于 RAG 的多轮对话流式接口（SSE）。

    返回 text/event-stream 格式，事件类型：
    - session_id: 会话 ID（首条）
    - token: 生成的 token 片段
    - chunk_refs: 代码引用列表（生成完成后）
    - deep_research_continue: Deep Research 非最终轮提示（仅 deep_research=True）
    - done: 完成信号
    - error: 错误信息
    """
    if deep_research:
        messages = [{"role": "user", "content": query}]

        async def dr_event_generator():
            try:
                async for event in handle_deep_research_stream(
                    db=db,
                    repo_id=repo_id,
                    query=query,
                    messages=messages,
                    session_id=session_id,
                    llm_provider=llm_provider,
                    llm_model=llm_model,
                ):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            dr_event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def event_generator():
        try:
            async for event in handle_chat_stream(
                db=db,
                repo_id=repo_id,
                query=query,
                session_id=session_id,
                llm_provider=llm_provider,
                llm_model=llm_model,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception(f"[ChatAPI] 流式对话失败: {e}")
            error_event = {"type": "error", "error": str(e)}
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
