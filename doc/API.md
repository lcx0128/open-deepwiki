# API 接口文档

> **版本**: 1.0.0 | **最后更新**: 2026-02-20
>
> Base URL: `http://localhost:8000`

---

## 概述

Open-DeepWiki REST API，使用 FastAPI 构建，支持 JSON 请求/响应和 SSE 流式推送。

---

## 健康检查

### GET /health

检查服务是否正常运行。

**响应 200**:
```json
{
    "status": "ok",
    "version": "1.0.0"
}
```

---

## 仓库管理

### POST /api/repositories

提交仓库处理任务。接收仓库 URL，创建 Repository 和 Task 记录，推入 Celery 队列，立即返回 Task ID。

**请求体**:
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `url` | string | 是 | Git 仓库完整 URL |
| `pat_token` | string | 否 | 私有仓库 PAT Token，用后即毁，不持久化 |
| `branch` | string | 否 | 目标分支，默认 `main` |
| `llm_provider` | string | 否 | LLM 供应商标识（openai/dashscope/gemini/custom） |
| `llm_model` | string | 否 | 模型名称 |

**请求示例**:
```json
{
    "url": "https://github.com/owner/repo",
    "pat_token": "ghp_xxxxxxxxxxxx",
    "branch": "main",
    "llm_provider": "openai",
    "llm_model": "gpt-4o"
}
```

**成功响应 201**:
```json
{
    "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "repo_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
    "status": "pending",
    "message": "任务已提交，正在排队处理"
}
```

**错误响应**:
| 状态码 | 错误码 | 场景 |
|--------|--------|------|
| 400 | `INVALID_URL` | URL 格式无效或无法解析平台 |
| 409 | `REPO_PROCESSING` | 该仓库已有任务在执行中 |
| 422 | `VALIDATION_ERROR` | Pydantic 校验失败 |

---

### GET /api/repositories

获取仓库列表，支持分页和状态过滤。

**查询参数**:
| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `page` | integer | 1 | 页码（从 1 开始） |
| `per_page` | integer | 20 | 每页数量（1-100） |
| `status` | string | - | 过滤状态：pending/cloning/ready/error/syncing |

**成功响应 200**:
```json
{
    "items": [
        {
            "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
            "url": "https://github.com/owner/repo",
            "name": "owner/repo",
            "platform": "github",
            "status": "ready",
            "last_synced_at": "2026-02-20T10:00:00Z",
            "created_at": "2026-02-19T08:00:00Z"
        }
    ],
    "total": 1,
    "page": 1,
    "per_page": 20
}
```

---

### POST /api/repositories/{repo_id}/reprocess — 强制全量重新处理

对已存在的仓库强制触发 FULL_PROCESS 任务（全量重新克隆、解析、向量化、生成 Wiki）。适用于任意阶段失败后需要完整重跑的场景，无需删除仓库重建。

**路径参数**:
| 参数 | 类型 | 说明 |
|------|------|------|
| `repo_id` | string | 仓库 UUID |

**请求体**:
```json
{
    "llm_provider": "dashscope",
    "llm_model": "qwen-plus"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `llm_provider` | string | 否 | LLM 供应商，不填则使用环境变量默认值 |
| `llm_model` | string | 否 | 模型名称 |

**成功响应 201**:
```json
{
    "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "repo_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
    "status": "pending",
    "message": "全量重新处理任务已提交"
}
```

**错误响应**:
| 状态码 | 错误码 | 场景 |
|--------|--------|------|
| 404 | - | 仓库不存在 |
| 409 | `REPO_PROCESSING` | 该仓库已有任务在执行中 |

---

### DELETE /api/repositories/{repo_id} — 删除仓库

删除指定仓库及其全部关联数据，适用于任意阶段卡死或需要完整重建的场景。

**操作范围：**
- 撤销所有活跃 Celery 任务（解除卡死状态）
- 数据库：Repository、Task、FileState、Wiki、WikiSection、WikiPage（全部级联删除）
- ChromaDB：删除该仓库的向量集合
- 本地磁盘：删除克隆目录

**成功响应 204**：无响应体

**错误响应**:
| 状态码 | 场景 |
|--------|------|
| 404 | 仓库不存在 |

**删除后恢复：** 重新提交同 URL（`POST /api/repositories`）即可完整重建。

---

## 任务管理

### GET /api/tasks/{task_id}

查询指定任务的当前状态。

**路径参数**:
| 参数 | 类型 | 说明 |
|------|------|------|
| `task_id` | string | 任务 UUID |

**成功响应 200**:
```json
{
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "repo_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
    "type": "full_process",
    "status": "parsing",
    "progress_pct": 35.5,
    "current_stage": "正在解析 Python 文件 (12/34)",
    "files_total": 34,
    "files_processed": 12,
    "error_msg": null,
    "created_at": "2026-02-20T10:30:00Z",
    "updated_at": "2026-02-20T10:32:15Z"
}
```

**错误响应**:
| 状态码 | 场景 |
|--------|------|
| 404 | 任务不存在 |

---

### GET /api/tasks/{task_id}/stream

SSE 实时进度推送端点。前端使用 `EventSource` 订阅此端点接收任务进度。

**请求头**:
```
Accept: text/event-stream
```

**SSE 消息格式**:
```
data: {"status":"cloning","progress_pct":10,"stage":"正在克隆仓库...","timestamp":"2026-02-20T10:30:05Z"}

data: {"status":"parsing","progress_pct":25,"stage":"解析 AST: src/main.py","files_processed":3,"files_total":20,"timestamp":"2026-02-20T10:30:15Z"}

data: {"status":"embedding","progress_pct":60,"stage":"生成向量嵌入 (15/20)","timestamp":"2026-02-20T10:31:00Z"}

data: {"status":"generating","progress_pct":85,"stage":"生成 Wiki: 核心模块解析","timestamp":"2026-02-20T10:32:00Z"}

data: {"status":"completed","progress_pct":100,"stage":"处理完成","wiki_id":"w-xxx","timestamp":"2026-02-20T10:33:00Z"}
```

**心跳**: 每秒发送 `: heartbeat` 注释行保持连接活跃。

**终止条件**: 收到 `status` 为 `completed`、`failed` 或 `cancelled` 时自动关闭。

**前端使用示例**:
```javascript
const es = new EventSource(`/api/tasks/${taskId}/stream`);
es.onmessage = (event) => {
    const data = JSON.parse(event.data);
    console.log(data.status, data.progress_pct);
    if (['completed', 'failed', 'cancelled'].includes(data.status)) {
        es.close();
    }
};
```

---

## Wiki 文档接口

### GET /api/wiki/{repo_id} — 获取 Wiki 内容

获取指定仓库的完整 Wiki 文档（含所有章节和页面）。

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| `repo_id` | string | 仓库 UUID |

**成功响应 (200)**:

```json
{
    "id": "w-xxx",
    "repo_id": "r-xxx",
    "title": "Flask 项目知识库",
    "llm_provider": "openai",
    "llm_model": "gpt-4o",
    "created_at": "2026-02-21T10:00:00Z",
    "sections": [
        {
            "id": "s-1",
            "title": "项目概述",
            "order_index": 0,
            "pages": [
                {
                    "id": "p-1",
                    "title": "架构总览",
                    "importance": "high",
                    "content_md": "# 架构总览\n\n...",
                    "relevant_files": ["src/app.py"],
                    "order_index": 0
                }
            ]
        }
    ]
}
```

**错误响应**:

| HTTP 状态码 | 场景 |
|------------|------|
| 404 | 仓库不存在或 Wiki 尚未生成 |

---

### POST /api/wiki/{repo_id}/regenerate — 重新生成 Wiki

触发指定仓库的 Wiki 重新生成任务，返回 task_id 供 SSE 跟踪进度。

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| `repo_id` | string | 仓库 UUID |

**请求体**:

```json
{
    "llm_provider": "dashscope",
    "llm_model": "qwen-plus",
    "pages": ["p-1", "p-3"]
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `llm_provider` | string | 否 | LLM 供应商，不填则使用环境变量默认值 |
| `llm_model` | string | 否 | 模型名称 |
| `pages` | array | 否 | 仅重新生成指定页面 ID 列表（预留，当前全量重生成） |

**成功响应 (201)**:

```json
{
    "task_id": "t-xxx",
    "message": "Wiki 重新生成任务已提交"
}
```

**错误响应**:

| HTTP 状态码 | 错误码 | 场景 |
|------------|-------|------|
| 404 | - | 仓库不存在 |
| 409 | `WIKI_REGENERATING` | 已有 Wiki 重生成任务在执行中 |

---

---

### DELETE /api/wiki/{repo_id} — 删除 Wiki 文档

删除指定仓库的 Wiki 文档（含所有章节和页面），保留仓库、ChromaDB 向量数据和任务记录。

**成功响应 204**：无响应体

**错误响应**:
| 状态码 | 场景 |
|--------|------|
| 404 | 仓库不存在或 Wiki 不存在 |

**删除后恢复：** 调用 `POST /api/wiki/{repo_id}/regenerate` 直接重新生成（跳过克隆/解析/向量化）。

---

### GET /api/wiki/{repo_id}/pages/{page_id} — 获取单个页面

获取指定 Wiki 页面的完整 Markdown 内容。

**成功响应 (200)**:

```json
{
    "id": "p-1",
    "title": "架构总览",
    "importance": "high",
    "content_md": "# 架构总览\n\n完整 Markdown 内容...",
    "relevant_files": ["src/app.py"],
    "order_index": 0
}
```
