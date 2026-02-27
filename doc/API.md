# API 接口文档

> **版本**: 1.5.0 | **最后更新**: 2026-02-26
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
            "failed_at_stage": null,
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

### POST /api/repositories/{repo_id}/sync — 触发增量同步

对已就绪仓库触发增量同步任务。执行 git pull 拉取最新代码，仅重新处理变更文件（新增/修改），清理已删除文件的向量数据，并重新生成 Wiki。

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
    "message": "增量同步任务已提交"
}
```

**错误响应**:
| 状态码 | 错误码 | 场景 |
|--------|--------|------|
| 400 | - | 仓库尚未完成初次克隆（local_path 为空） |
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

---

## 错误恢复指南

### 故障场景与推荐操作

| 故障场景 | 现象 | 推荐操作 |
|---------|------|---------|
| 任务卡死（Celery 进程被强制终止） | 任务状态永久停在 `cloning`/`parsing`/`embedding`/`generating` | 删除仓库后重新提交 |
| Wiki 内容需要更新（代码未变） | 已有 Wiki 但内容过时 | 调用 Wiki 重新生成接口 |
| Wiki 内容错误，需完全重来 | Wiki 存在但内容质量差 | 删除 Wiki 后重新生成 |
| LLM Key 失效后恢复 | 任务 `failed_at_stage="generating"` | 修复 Key 后调用 reprocess |
| 代码有新提交，需要同步 | 仓库已就绪但内容过时 | 重新提交同 URL（触发增量同步）|
| 任意阶段失败，需完整重跑 | `failed_at_stage` 为任意值 | 调用 reprocess（无需删除重建）|

### 典型恢复流程

#### 情景 1：进程崩溃，任务卡死

```
1. DELETE /api/repositories/{repo_id}     # 清除所有数据，释放卡死状态
2. POST /api/repositories                 # 重新提交，从头开始完整处理
3. GET /api/tasks/{task_id}/stream        # SSE 订阅进度
```

#### 情景 2：仅重新生成 Wiki（代码解析结果保留）

```
1. DELETE /api/wiki/{repo_id}             # 可选，清除旧 Wiki
2. POST /api/wiki/{repo_id}/regenerate    # 直接重生成（跳过克隆/解析/向量化）
3. GET /api/tasks/{task_id}/stream        # SSE 订阅进度
```

#### 情景 3：强制全量重新处理（保留仓库记录）

```
1. POST /api/repositories/{repo_id}/reprocess   # 强制 FULL_PROCESS
2. GET /api/tasks/{task_id}/stream               # SSE 订阅进度
```

### 任务重试机制

Celery 任务内置自动重试：**最多 2 次**，退避间隔 **30s → 60s**。重试前会将任务状态重置为 `pending`，前端可通过 SSE 观察到状态恢复。

若连续 2 次重试均失败，任务最终置为 `failed`，需手动通过上述恢复流程处理。

---

## 多轮对话接口（Module 5 RAG 层）

### POST /api/chat

基于 RAG 的多轮对话端点。
- `deep_research=false`（默认）：非流式，返回 JSON `ChatResponse`
- `deep_research=true`：流式 SSE，触发最多 5 轮迭代深度研究

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `repo_id` | string | 是 | 仓库 UUID |
| `session_id` | string | 否 | 会话 ID，首次调用传 null，后续调用传已有 session_id |
| `query` | string | 是 | 用户提问 |
| `llm_provider` | string | 否 | LLM 供应商（openai/dashscope/gemini/custom），不填则使用环境变量默认值 |
| `llm_model` | string | 否 | LLM 模型名称，不填则使用 gpt-4o |
| `deep_research` | boolean | 否 | 是否启用 Deep Research 模式，默认 false |
| `messages` | array | 否 | Deep Research 模式下传入的完整对话历史（`[{"role":"user","content":"..."}]`） |

**请求示例**:

```json
{
    "repo_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
    "session_id": null,
    "query": "请解释一下项目的核心架构是如何设计的？",
    "llm_provider": "openai",
    "llm_model": "gpt-4o"
}
```

**成功响应 (200)**:

```json
{
    "session_id": "c5d6e7f8-9a0b-1cde-f234-567890abcdef",
    "answer": "项目采用分层架构，包括四个核心模块...\n\n**代码引用**：\n- `src/app/main.py:1-50` 应用入口\n- `src/app/config.py:100-150` 配置管理",
    "chunk_refs": [
        {
            "file_path": "src/app/main.py",
            "start_line": 1,
            "end_line": 50,
            "name": "FastAPI Application"
        },
        {
            "file_path": "src/app/config.py",
            "start_line": 100,
            "end_line": 150,
            "name": "Settings Configuration"
        }
    ],
    "usage": {
        "prompt_tokens": 2500,
        "completion_tokens": 1200,
        "total_tokens": 3700
    }
}
```

**错误响应**:

| HTTP 状态码 | 错误码 | 场景 |
|-----------|-------|------|
| 400 | `INVALID_REQUEST` | 请求参数无效或缺少必填字段 |
| 404 | `REPO_NOT_FOUND` | 仓库不存在 |
| 404 | `NO_EMBEDDINGS` | 仓库未生成向量嵌入（Wiki 未就绪） |
| 500 | `LLM_ERROR` | LLM 调用失败或内部服务错误 |

**工作流程**:

1. **会话管理**：创建或恢复对话会话（Redis 存储，Key 格式 `conversation:{session_id}`，TTL 24 小时）
2. **查询融合**：将多轮对话上下文与当前问题合并为独立查询，解决代词指代问题
3. **Stage 1 检索**：ChromaDB 语义搜索，返回最相关的 10 个代码块摘要（仅包含 `file_path` 和 `start_line:end_line`）
4. **Stage 2 组装**：获取前 5 个最相关代码块的完整内容及符号名称
5. **Token 预算管理**：计算回答所需 Token 数，若超出上下文窗口则降级（移除 RAG 上下文或截断历史）
6. **LLM 生成**：调用 LLM 生成回答，回答中包含代码溯源注释（格式 `file_path:start_line-end_line`）
7. **持久化**：将本轮问答写回 Redis 会话，刷新 TTL

---

### GET /api/chat/stream

基于 RAG 的多轮对话端点（SSE 流式）。

**查询参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `repo_id` | string | 是 | 仓库 UUID |
| `query` | string | 是 | 用户提问 |
| `session_id` | string | 否 | 会话 ID，首次为空 |
| `llm_provider` | string | 否 | LLM 供应商，不填则使用环境变量默认值 |
| `llm_model` | string | 否 | LLM 模型，不填则使用 gpt-4o |
| `deep_research` | boolean | 否 | 是否启用 Deep Research 模式，默认 false |

**响应格式**: `text/event-stream`

**响应 Headers**:

```
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
```

**SSE 事件流示例**:

```
data: {"type": "session_id", "session_id": "c5d6e7f8-9a0b-1cde-f234-567890abcdef"}

data: {"type": "token", "content": "项目"}

data: {"type": "token", "content": "采用"}

data: {"type": "token", "content": "分层"}

data: {"type": "token", "content": "架构"}

data: {"type": "chunk_refs", "refs": [{"file_path": "src/app/main.py", "start_line": 1, "end_line": 50, "name": "FastAPI Application"}, {"file_path": "src/app/config.py", "start_line": 100, "end_line": 150, "name": "Settings Configuration"}]}

data: {"type": "done"}
```

**SSE 事件类型**:

| 事件类型 | 说明 | 字段 |
|---------|------|------|
| `session_id` | 会话 ID（第一条事件） | `session_id` (string) |
| `token` | 单个 token 片段 | `content` (string) |
| `chunk_refs` | 代码引用列表（生成完成后） | `refs` (array of objects) |
| `deep_research_continue` | Deep Research 非最终轮完成，提示继续（仅 `deep_research=true`） | `iteration` (int), `next_iteration` (int) |
| `done` | 流式生成完成信号 | 无 |
| `error` | 发生错误 | `error` (string) |

**前端使用示例（JavaScript）**:

```javascript
const eventSource = new EventSource(
    `/api/chat/stream?repo_id=${repoId}&query=${encodeURIComponent(query)}&session_id=${sessionId || ''}`
);

let answer = '';

eventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);

    switch (data.type) {
        case 'session_id':
            console.log('Session created:', data.session_id);
            // 保存 session_id 用于后续对话
            break;
        case 'token':
            answer += data.content;
            console.log('Token received:', data.content);
            break;
        case 'chunk_refs':
            console.log('Code references:', data.refs);
            break;
        case 'done':
            console.log('Generation complete');
            eventSource.close();
            break;
        case 'error':
            console.error('Error:', data.error);
            eventSource.close();
            break;
    }
};

eventSource.onerror = (err) => {
    console.error('SSE connection error', err);
    eventSource.close();
};
```

**错误响应**:

| HTTP 状态码 | 错误码 | 场景 |
|-----------|-------|------|
| 400 | `INVALID_REQUEST` | 查询参数无效或缺少必填参数 |
| 404 | `REPO_NOT_FOUND` | 仓库不存在 |
| 404 | `NO_EMBEDDINGS` | 仓库未生成向量嵌入 |
| 500 | `LLM_STREAMING_ERROR` | LLM 流式调用失败 |

---

### GET /api/chat/sessions/{session_id} — 获取会话历史

获取指定会话的历史消息列表，用于前端刷新页面后恢复对话记录。

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| `session_id` | string | 会话 UUID |

**成功响应 200**:

```json
{
    "session_id": "c5d6e7f8-9a0b-1cde-f234-567890abcdef",
    "messages": [
        {
            "id": "m1-uuid",
            "role": "user",
            "content": "这个项目的整体架构是什么？",
            "timestamp": "2026-02-25T10:00:00Z"
        },
        {
            "id": "m2-uuid",
            "role": "assistant",
            "content": "项目采用分层架构...",
            "chunk_refs": [
                {
                    "file_path": "app/main.py",
                    "start_line": 1,
                    "end_line": 50,
                    "name": "FastAPI Application"
                }
            ],
            "timestamp": "2026-02-25T10:00:05Z"
        }
    ]
}
```

**错误响应**:

| HTTP 状态码 | 场景 |
|-----------|------|
| 404 | 会话不存在或已过期（超过 24 小时） |

---

## 会话管理说明

### 会话存储与生命周期

- **存储位置**：Redis Hash，Key 格式 `conversation:{session_id}`
- **会话 TTL**：24 小时（每次对话后自动刷新）
- **首次对话**：客户端传入 `session_id: null`，服务端返回新生成的 session_id
- **续接对话**：后续调用传入已有 session_id，服务端恢复会话上下文
- **会话过期**：传入过期 session_id 时，服务端自动创建新会话（无报错提示）
- **会话内容**：包含历史消息列表、关联仓库 ID、累计 Token 用量、会话创建时间戳

### 会话上下文管理

- **多轮对话上下文**：保留最近 10 轮问答对（FIFO 队列）
- **上下文融合**：当前问题与历史问题合并，解决"它""这个"等代词指代问题
- **Token 限制**：历史上下文总 Token 数不超过 4000，超出时从最早对话开始截断
- **分离隔离**：不同 `repo_id` 的会话数据完全隔离，无法跨仓库共享

### 客户端集成说明（前端实现）

前端使用 **URL 路由**管理会话，而非 localStorage，避免 SPA 跨导航串会话的问题。

**路由结构**：`/chat/:repoId/:sessionId?`

**1. 新对话入口（从 Wiki 页面发起）**

```
WikiView → /chat/{repoId}?q={query}&dr=1
```

ChatView `onMounted` 检测到 `?q=` 时，**强制清空** Pinia store 并创建新会话。
首条 SSE 事件 `session_id` 返回后，执行 `router.replace` 将 URL 改为：
```
/chat/{repoId}/{sessionId}
```
（`replace` 不产生浏览器历史条目，返回键仍回到 WikiView）

**2. 刷新恢复**

URL 已包含 `sessionId` → 调用 `GET /api/chat/sessions/{sessionId}` → 历史消息填入 Pinia → 继续对话。
会话过期（404）时自动回退到空白聊天页。

**3. 切换仓库**

不同 `repoId` 的路由天然隔离，无需手动清空会话状态。

**4. Deep Research 完成后**

5 轮结束后前端自动将 `deepResearch` 切换为 `false`，后续对话变为普通模式。同一会话内不能再次发起 Deep Research（需新开对话）。

---

## MCP 服务器接口（Module 6）

> **版本**: v0.3.0 | **协议**: Model Context Protocol (MCP) | **传输**: stdio + streamable-http

Open-DeepWiki 提供标准 MCP 服务器，支持 Claude Code、Gemini CLI 等 AI 编程工具通过 MCP 协议直接访问代码库知识图谱。

### 启动方式

```bash
# stdio 模式（推荐用于本地 Claude Code）
python -m app.mcp_server --transport stdio

# HTTP 模式（用于远程 Agent 或 Docker 部署）
python -m app.mcp_server --transport http --port 8808 --host 0.0.0.0
```

### Claude Code 配置

```bash
# 注册 MCP 服务（stdio 模式，零网络开销）
claude mcp add deepwiki -- python -m app.mcp_server --transport stdio

# 使用示例（在 Claude Code 对话中）
# "use deepwiki to search for authentication logic in repo <repo_id>"
# "use deepwiki to get the architecture overview of repo <repo_id>"
```

### HTTP 鉴权

HTTP 模式支持可选 Bearer Token 鉴权。配置 `.env` 中 `MCP_AUTH_TOKEN` 后，所有 HTTP 请求必须携带：

```
Authorization: Bearer <MCP_AUTH_TOKEN>
```

若 `MCP_AUTH_TOKEN` 未配置，HTTP 模式允许匿名访问（适合内网环境）。

---

### MCP 工具列表

| 工具名 | 说明 | 主要参数 |
|--------|------|---------|
| `list_repositories` | 列出所有已就绪仓库 | 无 |
| `search_codebase` | 语义搜索代码（Stage 1 轻量导引） | `query`, `repo_id`, `top_k` |
| `get_code_chunks` | 获取完整代码块（Stage 2 按需加载） | `repo_id`, `chunk_ids` |
| `read_file` | 按行范围读取文件内容 | `repo_id`, `file_path`, `start_line`, `end_line` |
| `get_repository_overview` | 仓库架构总览（基于 Wiki） | `repo_id` |
| `get_wiki_content` | 获取 Wiki 章节内容 | `repo_id`, `section_title` |
| `get_dependency_graph` | 函数调用依赖图 | `repo_id`, `file_path` |
| `list_files` | 浏览仓库文件树 | `repo_id`, `path_prefix`, `extensions` |

---

### list_repositories

列出所有 `status=ready` 的已就绪仓库。

**参数**: 无

**返回示例**:
```json
[
    {
        "repo_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
        "name": "owner/repo",
        "url": "https://github.com/owner/repo",
        "status": "ready",
        "last_synced_at": "2026-02-25T10:00:00"
    }
]
```

---

### search_codebase

语义搜索代码库，返回相关代码片段的轻量导引（不含完整代码）。搜索基于 ChromaDB 向量检索（Stage 1）。

**参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | 是 | 自然语言搜索查询 |
| `repo_id` | string | 是 | 仓库 UUID |
| `top_k` | integer | 否 | 返回结果数量，默认 10，最大 50 |

**返回示例**:
```json
[
    {
        "chunk_id": "chunk-uuid-1",
        "name": "handle_login",
        "file_path": "app/api/auth.py",
        "node_type": "function_definition",
        "start_line": 45,
        "end_line": 78,
        "description": "async def handle_login(request: LoginRequest) -> LoginResponse:",
        "relevance_score": 0.92
    }
]
```

> **建议工作流**: `search_codebase` → 查看 `chunk_id` 和位置 → `get_code_chunks` 获取完整代码

---

### get_code_chunks

根据 `chunk_id` 列表获取完整代码块内容（Stage 2 按需加载）。

**参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `repo_id` | string | 是 | 仓库 UUID |
| `chunk_ids` | array[string] | 是 | 由 `search_codebase` 返回的 chunk_id 列表 |

**返回示例**:
```json
[
    {
        "chunk_id": "chunk-uuid-1",
        "header": "// File: app/api/auth.py (Lines 45-78)",
        "content": "async def handle_login(request: LoginRequest) -> LoginResponse:\n    ..."
    }
]
```

---

### read_file

读取仓库中指定文件的原始内容，支持行范围截取（1-indexed）。

**参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `repo_id` | string | 是 | 仓库 UUID |
| `file_path` | string | 是 | 相对于仓库根目录的文件路径 |
| `start_line` | integer | 否 | 起始行号（1-indexed），默认 1 |
| `end_line` | integer | 否 | 结束行号（含），默认 0 表示读到末尾 |

**返回示例**:
```json
{
    "file_path": "app/api/auth.py",
    "content": "from fastapi import APIRouter\n...",
    "language": "python",
    "start_line": 1,
    "end_line": 100,
    "total_lines": 250
}
```

**安全限制**: 不允许 `..` 路径穿越或绝对路径，文件访问严格限制在 `repos/{repo_id}/` 目录内。

---

### get_repository_overview

获取仓库整体架构概览（基于已生成的 Wiki 文档），适合在开始编码任务前快速了解项目结构。

**参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `repo_id` | string | 是 | 仓库 UUID |

**返回示例**:
```json
{
    "repo_id": "b2c3d4e5-...",
    "repo_name": "owner/repo",
    "repo_url": "https://github.com/owner/repo",
    "status": "ready",
    "last_synced_at": "2026-02-25T10:00:00",
    "wiki_title": "Open-DeepWiki 项目文档",
    "sections": [
        {
            "title": "架构总览",
            "pages": [
                {"title": "系统架构图", "importance": "high", "summary": "项目采用分层架构..."}
            ]
        }
    ],
    "total_sections": 6,
    "total_pages": 24
}
```

---

### get_wiki_content

获取 Wiki 文档内容，支持按章节标题筛选。

**参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `repo_id` | string | 是 | 仓库 UUID |
| `section_title` | string | 否 | 章节标题关键词（模糊匹配），为空则返回全部 |

**返回示例**:
```json
[
    {
        "section_title": "架构总览",
        "pages": [
            {
                "title": "系统架构图",
                "content_md": "# 系统架构图\n\n```mermaid\ngraph TD\n...\n```",
                "relevant_files": ["app/main.py", "app/config.py"],
                "importance": "high"
            }
        ]
    }
]
```

---

### get_dependency_graph

获取代码函数调用依赖图，从 ChromaDB 元数据重建（基于 AST 解析时记录的 `calls` 字段）。

**参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `repo_id` | string | 是 | 仓库 UUID |
| `file_path` | string | 否 | 过滤特定文件路径，为空则返回全仓库依赖图 |

**返回示例**:
```json
{
    "nodes": [
        {
            "id": "chunk-uuid-1",
            "name": "handle_login",
            "file": "app/api/auth.py",
            "type": "function_definition",
            "start_line": 45,
            "end_line": 78,
            "language": "python"
        }
    ],
    "edges": [
        {
            "from": "chunk-uuid-1",
            "to": "chunk-uuid-2",
            "type": "calls",
            "call_name": "verify_password"
        }
    ],
    "total_nodes": 128,
    "total_edges": 47
}
```

> **注意**: 依赖图准确性依赖 AST 静态分析，动态调用（如反射、`getattr`）无法检测。

---

### list_files

列出仓库中的文件，支持按路径前缀或扩展名筛选。

**参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `repo_id` | string | 是 | 仓库 UUID |
| `path_prefix` | string | 否 | 路径前缀过滤，如 `"app/services"` |
| `extensions` | array[string] | 否 | 扩展名列表，如 `[".py", ".ts"]` |

**返回示例**:
```json
[
    "app/main.py",
    "app/config.py",
    "app/services/chat_service.py",
    "app/services/embedder.py"
]
```

**限制**: 每次最多返回 500 个文件路径。

---

### MCP 错误响应格式

所有 MCP 工具在发生错误时返回统一格式：

```json
{
    "error": "错误描述信息",
    "tool": "工具名称"
}
```

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `MCP_AUTH_TOKEN` | HTTP 模式 Bearer Token（不配置则无鉴权） | 无 |
| `REPOS_BASE_DIR` | 仓库本地存储根目录 | `./repos` |
| `DATABASE_URL` | SQLAlchemy 异步数据库连接串 | `sqlite+aiosqlite:///./data/deepwiki.db` |
| `CHROMADB_PATH` | ChromaDB 数据目录 | `./data/chromadb` |
