# open-deepwiki

私有化代码知识库与 AI 智能体系统。对任意 Git 仓库进行深度语义索引，自动生成结构化 Wiki 文档，并支持基于代码库的多轮语义问答——完全部署在自有基础设施上。

## 功能特性

- **AST 语法级解析** — 使用 Tree-sitter 从语法树层面提取函数与类，拒绝正则截断。每个代码块均附带精确的文件路径与行号元数据。
- **Wiki 文档自动生成** — 直接从源代码生成按模块组织的技术文档与 Mermaid 架构图，支持导出为 Markdown 文件。
- **多轮代码库问答** — 双阶段 RAG 检索，回答自动附带代码溯源引用（如 `src/main.py:12-40`），会话历史持久化。
- **Deep Research 模式** — 最多五轮迭代研究：研究计划 → 增量深化 → 综合结论，适合复杂架构分析。
- **增量同步** — 仓库更新时，仅对变动文件重新索引，无需全量重处理。
- **多模型网关** — 支持 OpenAI、DashScope（通义千问）、Google Gemini 及任意 OpenAI 兼容的本地模型（Ollama、vLLM 等）。
- **MCP Server** — 通过 Model Context Protocol 暴露 8 个工具，允许 Claude Code 等外部 AI 智能体直接语义检索代码库。
- **实时进度推送** — 任务进度通过 SSE 推送，无需轮询。
- **私有化部署** — 单机单用户模式，数据不出本地（仅 LLM API 调用除外）。

## 技术栈

| 层级 | 技术选型 |
|---|---|
| 后端 API | FastAPI + Python 3.11 |
| 异步任务队列 | Celery + Redis |
| 关系型数据库 | SQLite（开发）/ PostgreSQL（生产），SQLAlchemy ORM |
| 向量数据库 | ChromaDB |
| 代码解析 | Tree-sitter |
| 前端 | Vue 3 + Pinia + Vite |
| LLM 网关 | 适配器模式（OpenAI / DashScope / Gemini / 本地模型） |
| MCP 协议 | Python MCP SDK（stdio + HTTP/SSE 双传输） |
| 容器化 | Docker Compose |

## 快速开始

### 前置条件

- Docker 与 Docker Compose
- 至少一个 LLM API Key（OpenAI、DashScope 或兼容接口）

### Docker 部署

```bash
git clone https://github.com/your-org/open-deepwiki.git
cd open-deepwiki
cp .env.example .env
# 编辑 .env，填入你的 LLM API Key 和 Base URL
mkdir -p data/chromadb repos
docker compose up -d
```

Web 界面启动后访问 `http://localhost`。MCP 服务自动以 HTTP 模式运行在 `http://localhost:8808`。

### 本地开发

**后端：**

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # 配置环境变量
alembic upgrade head
uvicorn app.main:app --reload --port 8000
# 另开终端启动 Worker（Windows 必须加 --pool=solo）：
celery -A app.celery_app worker -l info --pool=solo
```

**前端：**

```bash
cd frontend
npm install
npm run dev
```

前端开发服务器访问 `http://localhost:5173`。

## 配置说明

所有配置通过环境变量注入，复制 `.env.example` 为 `.env` 后按需修改：

| 变量名 | 说明 |
|---|---|
| `OPENAI_API_KEY` | OpenAI 或兼容接口的 API Key |
| `OPENAI_BASE_URL` | API Base URL（DashScope 需要） |
| `DASHSCOPE_API_KEY` | 阿里云百炼 API Key |
| `GOOGLE_API_KEY` | Google Gemini API Key |
| `DEFAULT_LLM_PROVIDER` | 默认 LLM 供应商（openai/dashscope/gemini/custom） |
| `DEFAULT_LLM_MODEL` | 默认模型名称，如 `gpt-4o`、`qwen-plus` |
| `WIKI_LANGUAGE` | Wiki 生成语言，默认 `Chinese`，支持任意自然语言名称 |
| `REDIS_URL` | Redis 连接地址 |
| `DATABASE_URL` | SQLAlchemy 数据库连接字符串 |
| `MCP_AUTH_TOKEN` | MCP HTTP 服务鉴权 Token（留空则无鉴权） |

完整变量列表及默认值参见 `.env.example`。

## 使用流程

1. 打开 Web 界面，粘贴 Git 仓库地址（公开仓库或附带 PAT Token 的私有仓库）。
2. 提交后，系统在后台自动克隆仓库、解析源文件并构建向量索引。
3. 浏览自动生成的 Wiki 文档，或点击导出按钮将 Wiki 下载为 Markdown 文件。
4. 在对话面板中对代码库发起多轮问答，开启 **Deep Research** 模式可进行深度架构分析。
5. 后续仓库有更新时，点击**增量更新**即可增量同步，仅处理变动文件。

## MCP 集成

将 open-deepwiki 作为 Claude Code 或其他 MCP 兼容客户端的上下文数据源。

### 本地 stdio 模式

复制项目根目录的 `.mcp.json.example` 为 `.mcp.json`，将路径替换为本机实际路径：

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

### 远程 HTTP 模式（Docker 部署后）

```json
{
  "mcpServers": {
    "open-deepwiki": {
      "url": "http://your-server:8808/mcp",
      "headers": {
        "Authorization": "Bearer your-secure-random-token"
      }
    }
  }
}
```

详细配置步骤参见 [doc/MCP.md](doc/MCP.md)。

### 可用工具（8 个）

| 工具名 | 功能 |
|---|---|
| `list_repositories` | 列出所有就绪仓库 |
| `search_codebase` | 语义搜索代码（返回 chunk 列表含相关性分数） |
| `get_code_chunks` | 按 chunk_id 获取完整代码内容 |
| `read_file` | 读取文件指定行范围 |
| `get_repository_overview` | 仓库概览 + Wiki 目录结构 |
| `get_wiki_content` | 读取 AI 生成的 Wiki 章节内容 |
| `get_dependency_graph` | 函数调用依赖图 |
| `list_files` | 列出仓库文件树（最多 500 个） |

## 开发进度

| 版本 | 里程碑 | 状态 |
|---|---|---|
| v0.1.0 | 基础设施层 + AST 解析层 | ✅ 已完成 |
| v0.2.0 | LLM 网关层 + Wiki 生成 + 前端层 | ✅ 已完成 |
| v0.3.0 | RAG 检索层 + MCP Server + 增量同步 + Docker 部署 | ✅ 已完成 |
| v1.0.0 | 正式发布（GA） | 规划中 |

## 贡献者

感谢以下贡献者对本项目的支持：

| 贡献者 | 贡献内容 |
|---|---|
| [ModCx](https://github.com/lcx0128) | 项目发起者与主要开发者 |
| [LeNotFound](https://github.com/LeNotFound) | 重写 Mermaid 生成规则 Prompt（PR #4） |

欢迎提交 Issue 和 Pull Request！

## 许可证

[MIT](LICENSE)
