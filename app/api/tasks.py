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
    if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.INTERRUPTED):
        async def final_event():
            stage = "已完成" if task.status == TaskStatus.COMPLETED else (task.error_msg or "已取消/已中断")
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
            # 订阅之后再读一次 DB，避免订阅前任务已完成导致错过终态事件
            from app.database import async_session_factory
            from app.models.task import Task as TaskModel
            async with async_session_factory() as fresh_db:
                fresh_task = await fresh_db.get(TaskModel, task_id)
            if fresh_task and fresh_task.status in (
                TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.INTERRUPTED
            ):
                stage = "已完成" if fresh_task.status == TaskStatus.COMPLETED else (
                    fresh_task.error_msg or "已取消"
                )
                payload = {
                    "status": fresh_task.status.value,
                    "progress_pct": fresh_task.progress_pct,
                    "stage": stage,
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                return

            # 发送当前状态作为初始事件
            # 使用 fresh_task（订阅后重读）作为初始事件，避免发送过期状态
            initial_source = fresh_task if fresh_task else task
            initial = {
                "status": initial_source.status.value,
                "progress_pct": initial_source.progress_pct,
                "stage": initial_source.current_stage or "等待处理",
            }
            yield f"data: {json.dumps(initial, ensure_ascii=False)}\n\n"

            _last_db_poll = asyncio.get_event_loop().time()
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
                        if parsed.get("status") in ("completed", "failed", "cancelled", "interrupted"):
                            break
                    except json.JSONDecodeError:
                        pass
                else:
                    # 无新消息时，每 3 秒做一次 DB 轮询作为兜底
                    # 防止 Redis 消息丢失导致前端永久停留在旧状态
                    now = asyncio.get_event_loop().time()
                    if now - _last_db_poll >= 3.0:
                        _last_db_poll = now
                        try:
                            async with async_session_factory() as poll_db:
                                polled = await poll_db.get(TaskModel, task_id)
                            if polled:
                                poll_payload = {
                                    "status": polled.status.value,
                                    "progress_pct": polled.progress_pct,
                                    "stage": polled.current_stage or "",
                                }
                                yield f"data: {json.dumps(poll_payload, ensure_ascii=False)}\n\n"
                                if polled.status in (
                                    TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.INTERRUPTED
                                ):
                                    break
                        except Exception:
                            pass
                    yield ": heartbeat\n\n"
                    await asyncio.sleep(1)
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    return StreamingResponse(event_generator(), media_type="text/event-stream")
