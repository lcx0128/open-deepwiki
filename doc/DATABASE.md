# 数据库设计文档

> **版本**: 1.0.0 | **最后更新**: 2026-02-20

本文档描述 Open-DeepWiki 系统的关系型数据库 Schema，采用 SQLAlchemy 2.0 ORM + Alembic 迁移管理。

---

## 概述

系统使用三张核心表构成数据层基础：

| 表名 | 用途 |
|------|------|
| `repositories` | 记录被管理的代码仓库元信息 |
| `tasks` | 追踪每个异步后台任务的完整生命周期 |
| `file_states` | 记录每个源文件的处理状态，是增量同步的核心依据 |

---

## repositories 表

**业务含义**: 记录每个被管理的代码仓库的基础元信息，是整个系统的根实体。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | VARCHAR(36) | PK, UUID4 | 全局唯一标识，格式为 UUID v4 字符串 |
| `url` | VARCHAR(512) | NOT NULL, UNIQUE | 仓库完整 URL，防止重复提交同一仓库 |
| `name` | VARCHAR(255) | NOT NULL | 仓库名称，格式为 `owner/repo` |
| `platform` | ENUM | NOT NULL, DEFAULT 'github' | 代码托管平台：github/gitlab/bitbucket/custom |
| `default_branch` | VARCHAR(128) | DEFAULT 'main' | 默认分支名称，用于增量同步的目标分支 |
| `local_path` | VARCHAR(1024) | NULLABLE | 本地克隆路径，克隆完成后填入 |
| `status` | ENUM | NOT NULL, DEFAULT 'pending' | 仓库状态机：pending/cloning/ready/error/syncing |
| `last_synced_at` | DATETIME | NULLABLE | 最后同步时间戳，首次克隆前为 NULL |
| `created_at` | DATETIME | NOT NULL | 记录创建时间，自动填充 |
| `updated_at` | DATETIME | NOT NULL | 记录最后更新时间，写入时自动更新 |

**状态机流转**:
```
PENDING → CLONING → READY
                  ↘ ERROR
         READY → SYNCING → READY
                          ↘ ERROR
```

**索引**:
- 主键索引: `id`
- 唯一索引: `url`（防止重复仓库）

---

## tasks 表

**业务含义**: 追踪每个异步后台任务（克隆、解析、生成）的完整生命周期状态，是进度反馈的数据来源。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | VARCHAR(36) | PK, UUID4 | 全局唯一任务标识 |
| `repo_id` | VARCHAR(36) | FK(repositories.id) CASCADE | 所属仓库 ID |
| `type` | ENUM | NOT NULL, DEFAULT 'full_process' | 任务类型：full_process/incremental_sync/wiki_regenerate/parse_only |
| `status` | ENUM | NOT NULL, DEFAULT 'pending' | 任务状态机 |
| `progress_pct` | FLOAT | NOT NULL, DEFAULT 0.0 | 进度百分比，范围 0.0 ~ 100.0 |
| `current_stage` | VARCHAR(64) | NULLABLE | 当前执行阶段的人类可读描述 |
| `error_msg` | TEXT | NULLABLE | 失败时的错误信息 |
| `celery_task_id` | VARCHAR(255) | NULLABLE | Celery 分配的任务 ID，用于取消操作 |
| `files_total` | INTEGER | DEFAULT 0 | 待处理文件总数 |
| `files_processed` | INTEGER | DEFAULT 0 | 已处理文件数 |
| `created_at` | DATETIME | NOT NULL | 任务创建时间 |
| `updated_at` | DATETIME | NOT NULL | 最后更新时间 |

**状态机流转**:
```
PENDING → CLONING → PARSING → EMBEDDING → GENERATING → COMPLETED
    ↓         ↓         ↓          ↓            ↓
  FAILED   FAILED    FAILED     FAILED       FAILED
PENDING → CANCELLED（用户主动取消）
```

**索引**:
- 主键索引: `id`
- 普通索引: `repo_id`（按仓库查询任务列表）

**外键约束**: `repo_id` → `repositories.id`，`ON DELETE CASCADE`（仓库删除时级联删除所有相关任务）

---

## file_states 表

**业务含义**: 记录每个源文件的最后处理状态，是增量同步机制的核心数据依据。通过 `last_commit_hash` 实现幂等性检查点，通过 `chunk_ids_json` 支持 ChromaDB 的精准定向删除。

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | VARCHAR(36) | PK, UUID4 | 全局唯一标识 |
| `repo_id` | VARCHAR(36) | FK(repositories.id) CASCADE | 所属仓库 ID |
| `file_path` | VARCHAR(1024) | NOT NULL | 相对于仓库根目录的文件路径，如 `src/auth/login.py` |
| `last_commit_hash` | VARCHAR(40) | NOT NULL | 该文件最后一次被处理时的 Git commit SHA（40位哈希） |
| `chunk_ids_json` | TEXT | NOT NULL, DEFAULT '[]' | JSON 数组，存储该文件在 ChromaDB 中的所有 chunk ID |
| `chunk_count` | INTEGER | DEFAULT 0 | chunk 数量，快速统计无需解析 JSON |
| `file_hash` | VARCHAR(64) | NULLABLE | 文件内容 SHA256 哈希，用于内容去重 |
| `created_at` | DATETIME | NOT NULL | 记录创建时间 |
| `updated_at` | DATETIME | NOT NULL | 最后更新时间 |

**chunk_ids_json 格式示例**:
```json
[
    "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "b2c3d4e5-f6a7-8901-bcde-f12345678901"
]
```

**索引**:
- 主键索引: `id`
- 普通索引: `repo_id`（按仓库查询文件状态列表）
- 唯一复合索引: `(repo_id, file_path)`，名称 `uq_file_state_repo_path`

**外键约束**: `repo_id` → `repositories.id`，`ON DELETE CASCADE`

**设计决策**: 使用 JSON 字符串而非关联表存储 chunk IDs，原因：
1. chunk ID 仅在增量删除时需要，不需要反向查询
2. 避免引入多对多关联表增加复杂度
3. SQLite 和 PostgreSQL 均支持 JSON 字段操作

---

## 表关系图

```
repositories (1) ──── (N) tasks
     │
     └──── (N) file_states
```

---

## 迁移管理

使用 Alembic 管理数据库 Schema 变更：

```bash
# 应用最新迁移
alembic upgrade head

# 回滚一个版本
alembic downgrade -1

# 生成新迁移文件
alembic revision --autogenerate -m "add_new_column"

# 查看当前版本
alembic current
```
