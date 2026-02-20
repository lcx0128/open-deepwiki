import json
import asyncio
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.task import Task, TaskStatus
from app.schemas.repository import TaskStatusResponse

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get(
    "/{task_id}",
    response_model=TaskStatusResponse,
    summary="查询任务状态",
)
async def get_task_status(task_id: str, db: AsyncSession = Depends(get_db)):
    """查询指定任务的当前状态。"""
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return TaskStatusResponse.model_validate(task)


@router.get(
    "/{task_id}/stream",
    summary="SSE 实时进度推送",
    response_class=StreamingResponse,
)
async def stream_task_progress(task_id: str, db: AsyncSession = Depends(get_db)):
    """SSE 端点：实时推送任务进度事件流。"""
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 如果任务已是终态，直接返回最终状态
    if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
        async def final_event():
            stage = "已完成" if task.status == TaskStatus.COMPLETED else (task.error_msg or "已取消")
            payload = {
                "status": task.status.value,
                "progress_pct": task.progress_pct,
                "stage": stage,
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        return StreamingResponse(final_event(), media_type="text/event-stream")

    # 订阅 Redis Pub/Sub 频道
    async def event_generator():
        from app.core.redis_client import get_redis
        redis = await get_redis()
        pubsub = redis.pubsub()
        channel = f"task_progress:{task_id}"
        await pubsub.subscribe(channel)

        try:
            # 发送当前状态作为初始事件
            initial = {
                "status": task.status.value,
                "progress_pct": task.progress_pct,
                "stage": task.current_stage or "等待处理",
            }
            yield f"data: {json.dumps(initial, ensure_ascii=False)}\n\n"

            # 持续监听
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message and message["type"] == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    yield f"data: {data}\n\n"

                    # 检查是否为终态消息
                    try:
                        parsed = json.loads(data)
                        if parsed.get("status") in ("completed", "failed", "cancelled"):
                            break
                    except json.JSONDecodeError:
                        pass
                else:
                    # 发送心跳保持连接
                    yield ": heartbeat\n\n"
                    await asyncio.sleep(1)
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    return StreamingResponse(event_generator(), media_type="text/event-stream")
