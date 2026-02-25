# MCP 服务配置指南

本文档面向新手，从零开始配置 open-deepwiki 的 MCP 服务，使 Claude Code、Gemini CLI 等 AI 编程工具能通过 MCP 协议直接查询代码库知识。

---

## 目录

- [什么是 MCP](#什么是-mcp)
- [前置条件](#前置条件)
- [第一步：初始化项目](#第一步初始化项目)
- [第二步：启动 MCP 服务](#第二步启动-mcp-服务)
- [第三步：接入 Claude Code](#第三步接入-claude-code)
- [第四步：配置 DeepWiki Skill](#第四步配置-deepwiki-skill)
- [可选：HTTP 模式（远程接入）](#可选http-模式远程接入)
- [工具列表速查](#工具列表速查)
- [常见问题](#常见问题)

---

## 什么是 MCP

MCP（Model Context Protocol）是一种标准协议，让 AI 工具（如 Claude Code）能调用外部服务获取上下文信息。

open-deepwiki 的 MCP 服务暴露 8 个工具，让 AI 在编码时可以：

- 语义搜索代码库（不用逐文件阅读）
- 获取函数/类的完整实现
- 查看模块依赖调用关系
- 读取 AI 生成的 Wiki 文档

```
Claude Code ──MCP协议──▶ open-deepwiki MCP Server ──▶ ChromaDB / SQLite / 文件系统
```

---

## 前置条件

| 依赖 | 版本要求 | 说明 |
|------|---------|------|
| Python | ≥ 3.10 | MCP server 运行环境 |
| open-deepwiki | 已部署 | 需要先处理至少一个仓库到 `READY` 状态 |
| Claude Code | 最新版 | 或其他支持 MCP 的客户端 |

> **注意**：MCP server 本身不需要 uvicorn/Redis/Celery 在运行，只需要数据库文件和 ChromaDB 数据存在。

---

## 第一步：初始化项目

### 1.1 安装依赖

```bash
cd open-deepwiki
pip install -r requirements.txt
```

### 1.2 初始化数据库

```bash
alembic upgrade head
```

如果是首次使用，还需要启动完整服务处理至少一个代码仓库：

```bash
# 启动后端服务
uvicorn app.main:app --reload --port 8000

# 另一个终端：启动 Celery Worker（Windows 必须加 --pool=solo）
celery -A app.celery_app worker -l info --pool=solo

# 在 http://localhost:8000 提交一个 GitHub 仓库 URL，等待状态变为 READY
```

### 1.3 配置环境变量

复制 `.env.example` 并填写必要配置：

```bash
cp .env.example .env
```

`.env` 最小配置示例：

```bash
DATABASE_URL=sqlite+aiosqlite:///./data/deepwiki.db
CHROMADB_PATH=./data/chromadb
REPOS_BASE_DIR=./repos

# LLM 配置（MCP server 的 search_codebase 需要 Embedding API）
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1

# 可选：HTTP 模式鉴权 Token
# MCP_AUTH_TOKEN=your-secret-token
```

> **DashScope（阿里云）用户**：
> ```bash
> OPENAI_API_KEY=sk-xxx        # DashScope API Key
> OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
> ```

---

## 第二步：启动 MCP 服务

open-deepwiki MCP server 支持两种传输模式：

### stdio 模式（推荐，用于本地 Claude Code）

```bash
python -m app.mcp_server --transport stdio
```

stdio 模式下，Claude Code 会自动启动和管理这个进程，**无需手动运行**，只需在配置文件中指定命令即可（见第三步）。

### HTTP 模式（用于远程 Agent 或调试）

```bash
python -m app.mcp_server --transport http --port 8808
```

启动后 MCP server 监听 `http://localhost:8808`。

---

## 第三步：接入 Claude Code

### 3.1 创建 MCP 配置文件

参考项目根目录的 `.mcp.json.example`，在项目根目录创建自己的 `.mcp.json`：

```bash
cp .mcp.json.example .mcp.json
```

然后编辑 `.mcp.json`，将路径替换为你本机的实际路径：

```json
{
  "mcpServers": {
    "open-deepwiki": {
      "command": "python",
      "args": ["-m", "app.mcp_server", "--transport", "stdio"],
      "cwd": "/your/path/to/open-deepwiki",
      "env": {
        "PYTHONPATH": "/your/path/to/open-deepwiki"
      }
    }
  }
}
```

**路径填写说明：**

- **macOS/Linux**：填写绝对路径，如 `/home/user/projects/open-deepwiki`
- **Windows**：填写绝对路径，反斜杠需转义，如 `E:\\Projects\\open-deepwiki`；建议将 `python` 替换为 Python 完整路径，如 `C:\\Python310\\python.exe`

> `.mcp.json` 包含本机路径，已加入 `.gitignore`，**请勿提交到版本库**。

### 3.2 重启 Claude Code

保存 `.mcp.json` 后，**完全退出并重新启动 Claude Code**。

### 3.3 验证连接

在 Claude Code 对话框中输入：

```
用 list_repositories 列出所有仓库
```

如果返回仓库列表（即使是空列表 `[]`），说明 MCP 连接成功。

---

## 第四步：配置 DeepWiki Skill

DeepWiki Skill 是一个工作流提示词，告诉 Claude 在分析代码库时按最优顺序调用 MCP 工具。

### 4.1 创建 Skill 文件

在项目根目录创建以下目录和文件：

```
.claude/
└── skills/
    └── deepwiki.md
```

```bash
mkdir -p .claude/skills
```

### 4.2 Skill 文件内容

将以下内容写入 `.claude/skills/deepwiki.md`：

````markdown
# DeepWiki — 代码库上下文助手

使用 open-deepwiki MCP 工具快速理解大型代码库，辅助编码任务。

## 标准工作流

### 第一步：确定目标仓库
调用 `list_repositories` 获取所有 READY 仓库，根据名称选择目标 repo_id。

### 第二步：获取整体架构
调用 `get_repository_overview(repo_id)` 了解语言构成、文件数、Wiki 目录结构。

### 第三步：语义搜索相关代码
调用 `search_codebase(query="用户问题的核心概念", repo_id, top_k=10)`
- query 用功能描述，不用文件名
- 返回 chunk 列表，含 chunk_id、file_path、start_line、end_line、relevance_score

### 第四步：获取完整代码内容
调用 `get_code_chunks(repo_id, chunk_ids=[...])` 取 relevance_score 最高的 3-5 个 chunk_id。

### 第五步（按需）：读取更多上下文
调用 `read_file(repo_id, file_path, start_line, end_line)` 读取指定行范围。

### 第六步（按需）：查看依赖关系
调用 `get_dependency_graph(repo_id, file_path="app/services/")` 分析调用关系。

### 第七步（按需）：浏览文件树
调用 `list_files(repo_id, path_prefix="app/", extensions=[".py"])` 了解目录结构。

### 第八步（按需）：查阅 Wiki 文档
调用 `get_wiki_content(repo_id, section_title="架构")` 读取 AI 生成的设计文档。

## 注意事项
- chunk_id 必须来自 search_codebase 返回结果，不能自行构造
- relevance_score > 0.7 才值得深入阅读
- file_path 是相对于仓库根目录的路径
- 引用代码位置时使用 `file_path:line_number` 格式
````

### 4.3 触发方式

在 Claude Code 对话中输入 `/deepwiki`，或直接描述任务：

```
帮我理解这个项目的 RAG 检索是怎么实现的
用 deepwiki 分析 chat_service.py 的调用链
```

> `.claude/` 目录包含本地工作流配置，已加入 `.gitignore`，**请勿提交到版本库**。

---

## 可选：HTTP 模式（远程接入）

如果需要在远程服务器部署，或接入其他支持 MCP 的客户端，使用 HTTP 模式。

### 启动带鉴权的 HTTP 服务

在 `.env` 中设置：

```bash
MCP_AUTH_TOKEN=your-secret-token
```

启动：

```bash
python -m app.mcp_server --transport http --host 0.0.0.0 --port 8808
```

### 请求示例（curl）

```bash
curl -X POST http://localhost:8808/mcp \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-secret-token" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "list_repositories",
      "arguments": {}
    },
    "id": 1
  }'
```

不设置 `MCP_AUTH_TOKEN` 时，HTTP 模式无需鉴权（适合内网使用）。

---

## 工具列表速查

| 工具名 | 功能 | 关键参数 |
|--------|------|---------|
| `list_repositories` | 列出所有 READY 仓库 | 无 |
| `search_codebase` | 语义搜索代码 | `query`, `repo_id`, `top_k`（默认10，最大50）|
| `get_code_chunks` | 获取 chunk 完整代码 | `repo_id`, `chunk_ids`（来自 search 结果）|
| `read_file` | 读取文件指定行范围 | `repo_id`, `file_path`, `start_line`, `end_line`（0=到末尾）|
| `get_repository_overview` | 仓库概览 + Wiki 目录 | `repo_id` |
| `get_wiki_content` | 读取 Wiki 章节内容 | `repo_id`, `section_title`（可选，空字符串返回全部）|
| `get_dependency_graph` | 函数调用依赖图 | `repo_id`, `file_path`（可选，过滤到某目录）|
| `list_files` | 列出仓库文件（最多500）| `repo_id`, `path_prefix`, `extensions` |

---

## 常见问题

**MCP 工具返回"仓库目录不存在"**

`REPOS_BASE_DIR` 配置路径与实际克隆目录不一致，检查 `.env` 中的 `REPOS_BASE_DIR`。

**没有 READY 状态的仓库**

数据库中尚无处理完成的仓库，先通过主服务（`http://localhost:8000`）提交仓库并等待完成。

**`search_codebase` 报 Embedding API 错误**

该工具需调用 Embedding API，检查 `.env` 中的 `OPENAI_API_KEY` 和 `OPENAI_BASE_URL`。

**Claude Code 找不到 MCP 工具**

1. 确认 `.mcp.json` 在项目根目录（不是子目录）
2. Windows 路径中的反斜杠已双写（`\\`）
3. 完全退出 Claude Code 再重新打开

**stdio 模式如何查看日志**

stdio 模式日志写入 `stderr`，Claude Code 通常不展示。如需调试，临时切换 HTTP 模式：

```bash
python -m app.mcp_server --transport http --port 8808
```

用 curl 直接测试工具响应。
