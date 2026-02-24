# 任务处理管线设计文档

本文档描述仓库处理任务的完整设计思路，包括数据流转、状态机、幂等机制和关键约束。

---

## 一、总体流程

用户通过 `POST /api/repositories` 提交仓库 URL，系统异步完成以下四个阶段：

```
[API 接收] → [Celery 队列] → 阶段1:克隆 → 阶段2:解析 → 阶段3:向量化 → 阶段4:Wiki生成 → [完成]
```

每个阶段完成后立即更新数据库状态，并通过 Redis Pub/Sub 向前端推送进度事件。

---

## 二、数据模型关系

```
Repository（仓库）
  │  一对多
  ├── Task（任务）          ← 记录每次处理的生命周期和进度
  └── FileState（文件状态） ← 每个代码文件的哈希和 chunk IDs
                                   │
                                   └── ChromaDB Collection  ← 向量数据（独立存储）
```

### Repository
| 字段 | 含义 |
|---|---|
| `url` | 仓库原始 URL（唯一键，用于判断是否已存在） |
| `status` | `pending` / `cloning` / `ready` / `error` |
| `local_path` | 克隆到本地的路径，格式为 `./repos/{repo_id}` |
| `last_synced_at` | 最近一次成功处理完成的时间 |

### Task
| 字段 | 含义 |
|---|---|
| `type` | `full_process`（首次）或 `incremental_sync`（再次提交） |
| `status` | 详见第三节状态机 |
| `progress_pct` | 0–100，各阶段固定起始值（见下表） |
| `current_stage` | 当前阶段的人类可读描述 |
| `failed_at_stage` | 失败时所处阶段（`cloning`/`parsing`/`embedding`/`generating`） |
| `error_msg` | 失败原因，经过 Token 脱敏处理 |

### FileState
| 字段 | 含义 |
|---|---|
| `file_path` | 相对于仓库根目录的路径，用 `/` 分隔 |
| `file_hash` | 文件内容的 SHA-256，用于增量同步去重 |
| `chunk_ids_json` | 该文件生成的所有 ChunkNode ID 列表（JSON 数组） |
| `last_commit_hash` | 写入时的 HEAD commit hash |

**重要**：FileState 仅在 Embedding **全部成功后**才写入，不在解析阶段写入。

---

## 三、Task 状态机

### 状态值与对应进度

| 状态 | `progress_pct` | 触发条件 |
|---|---|---|
| `pending` | 0 | Task 记录刚创建，尚未被 Worker 拾取 |
| `cloning` | 5 | Worker 开始 git clone |
| `parsing` | 20 | clone 成功，开始 AST 解析 |
| `embedding` | 50 | 解析完成，开始调用 Embedding API |
| `generating` | 75 | 向量化完成，开始 Wiki 生成 |
| `completed` | 100 | 全部阶段成功 |
| `failed` | 不变 | 任意阶段出现不可恢复错误 |
| `cancelled` | 不变 | 用户主动取消（预留） |

### 状态转换规则（严格模式）

```
pending
  └─[Worker 拾取]─→ cloning
       ├─[git clone 失败]─→ failed  (failed_at_stage = "cloning")
       └─[成功]─→ parsing
            ├─[FULL_PROCESS 且 chunks = 0]─→ failed  (failed_at_stage = "parsing")
            └─[成功，chunks > 0]─→ embedding
                 ├─[API 错误，3次重试后仍失败]─→ failed  (failed_at_stage = "embedding")
                 └─[成功]─→ generating
                      ├─[NotImplementedError]─→ 跳过（不失败，Module 3 未实现）
                      └─[成功 / 跳过]─→ completed
```

### INCREMENTAL_SYNC 的特殊规则

- 若 0 个文件变更（全部 hash 匹配） → 直接进入 `embedding` 阶段（跳过），然后 `generating`，最终 `completed`
- INCREMENTAL_SYNC 允许 chunks = 0（表示无变更），不视为失败

---

## 四、任务类型判断

`POST /api/repositories` 提交时：

```python
if 该 URL 的 Repository 已存在:
    if 有进行中的 Task → 返回 409 CONFLICT
    else → 创建 INCREMENTAL_SYNC Task
else:
    创建新 Repository + FULL_PROCESS Task
```

**强制全量重建**（`force_full=True`）：
- 仅 `FULL_PROCESS` 类型触发
- 解析时忽略 FileState 的 hash 比较，强制重新解析所有文件

---

## 五、解析阶段（Parser）逻辑

```
for 每个代码文件:
    1. 检测语言（按扩展名，不支持则跳过）
    2. 读取文件内容，计算 SHA-256 hash
    3. 查询 FileState:
       - 若存在且 hash 相同 且 非 force_full → 跳过（文件未变更）
       - 否则 → 继续解析
    4. Tree-sitter AST 解析 → 提取函数/类节点 → ChunkNode 列表
    5. 对超大 chunk 滑动窗口切分
    6. 记录 file_hashes[relative_path] = file_hash

return (all_chunks, file_hashes)
```

**Parser 不写 FileState**。只读取它（用于 hash 比较），写入职责交给 Embedder。

### 支持的语言

| 语言 | 解析节点类型 |
|---|---|
| Python | `function_definition`, `class_definition` |
| JavaScript / TypeScript | `function_declaration`, `class_declaration`, `arrow_function` |
| Go | `function_declaration`, `method_declaration` |
| Java | `method_declaration`, `class_declaration` |
| Rust | `function_item`, `impl_item` |

---

## 六、向量化阶段（Embedder）逻辑

```
按批次（每批 ≤ 10 条，DashScope 限制）:
    1. 调用 Embedding API（text-embedding-v3）
    2. 失败 → 3次指数退避重试（2s→4s→8s）
    3. 仍失败 → raise（严格模式，不降级）
    4. 成功 → 写入 ChromaDB（带向量）

全部批次成功后:
    for 每个 file_path:
        if FileState 已存在 → 更新 file_hash + chunk_ids + commit_hash
        else              → 创建新 FileState 记录
    await db.flush()
```

**FileState 写入时机**：仅在所有批次向量化成功后才写，确保原子性。若中途失败，已写入 ChromaDB 的向量不会回滚（ChromaDB 无事务），但 FileState 不会更新，下次任务会重新处理这些文件。

---

## 七、进度事件推送

每个阶段切换时，同时做两件事：
1. `_update_task(db, ...)` — 更新数据库 Task 记录
2. `_publish(task_id, ...)` — 向 Redis 频道 `progress:{task_id}` 发布 JSON 事件

事件格式：
```json
{
  "status": "embedding",
  "progress_pct": 62.5,
  "stage": "向量化进度: 25/38",
  "timestamp": "2026-02-21T10:00:00Z"
}
```

前端通过 `EventSource` 订阅 `/api/tasks/{task_id}/progress` SSE 端点接收实时进度。

---

## 八、错误处理与脱敏

所有 `error_msg` 在写入数据库前执行正则脱敏：
- `https://oauth2:{token}@` → `https://[REDACTED]@`
- `ghp_xxxxx` → `[REDACTED]`
- `glpat-xxxxx` → `[REDACTED]`

日志层通过 `TokenScrubFilter` 对所有 handler 做相同处理，支持 `str` 和 `dict` 两种 `record.args` 格式。

---

## 九、并发控制

| 层级 | 机制 | 参数 |
|---|---|---|
| Celery 任务并发 | Worker 进程数 | Windows 使用 `--pool=solo` |
| Embedding API 并发 | `asyncio.Semaphore` | `MAX_CONCURRENT_LLM_CALLS=10` |
| API 请求重试 | tenacity 指数退避 | 最多3次，2s→4s→8s |
| 同一仓库防重复 | 提交时检查 active Task | 返回 409 + existing_task_id |

---

## 十、增量同步设计

增量同步的核心是 `FileState.file_hash`：

```
上次成功处理 → FileState 写入（file_hash = H_old）
                    ↓
代码仓库更新（某些文件变更）
                    ↓
INCREMENTAL_SYNC 触发
  ├── 文件 hash 未变 → FileState.file_hash == 计算结果 → 跳过
  └── 文件 hash 已变 → 重新解析 + 向量化 → 更新 FileState
```

**当前未实现**（计划在 Module 3 后）：
- 已删除文件的 ChromaDB 向量清理（需读取 git diff `D` 状态）
- 文件重命名检测
