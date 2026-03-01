"""
模块三：Wiki 生成服务。
基于 ChromaDB 中的 ChunkNode 向量数据，通过两阶段 LLM 调用生成 Wiki 文档。
"""
import asyncio
import json as _json
import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import List, Optional, Callable, Dict, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.services.llm.factory import create_adapter
from app.services.embedder import get_collection
from app.schemas.llm import LLMMessage
from app.models.wiki import Wiki, WikiSection, WikiPage
from app.models.repository import Repository
from app.services.mermaid_validator import (
    validate_and_fix_mermaid,
    process_diagram_specs,
    retry_failed_diagram_specs,
)
from app.services.token_degradation import is_token_overflow, generate_with_degradation
from app.config import settings

logger = logging.getLogger(__name__)

# ============================================================
# 图表生成 System Prompt（JSON spec 格式）
# ============================================================
MERMAID_CONSTRAINT_PROMPT = """
╔══════════════════════════════════════════════════════════════════╗
║  DIAGRAM OUTPUT FORMAT — STRICT REQUIREMENT                      ║
║                                                                  ║
║  ✗ FORBIDDEN: ```mermaid  (raw Mermaid will cause syntax errors) ║
║  ✓ REQUIRED:  ```diagram-spec  (JSON → assembled by Python)      ║
╚══════════════════════════════════════════════════════════════════╝

NEVER output a ```mermaid code block. It will break.
ALWAYS output diagrams as ```diagram-spec JSON. The assembler handles:
  - ASCII node IDs (auto-sanitized)
  - Chinese label quoting in erDiagram (auto-added)
  - Node label length limits (auto-truncated)
  - flowchart direction (enforced)
You do NOT need to worry about any Mermaid syntax rules — just write valid JSON.

━━━ FLOWCHART (架构图 / 模块关系图) ━━━
```diagram-spec
{
  "type": "flowchart",
  "direction": "TD",
  "nodes": [
    {"id": "Client",  "label": "客户端",        "shape": "rect"},
    {"id": "API",     "label": "API网关",        "shape": "rect"},
    {"id": "Worker",  "label": "Celery Worker", "shape": "stadium"},
    {"id": "DB",      "label": "PostgreSQL",    "shape": "subroutine"},
    {"id": "Cache",   "label": "Redis",         "shape": "subroutine"}
  ],
  "edges": [
    {"from_id": "Client", "to_id": "API",    "label": "HTTP请求"},
    {"from_id": "API",    "to_id": "Worker", "label": "发布任务"},
    {"from_id": "Worker", "to_id": "DB",     "label": "状态更新"},
    {"from_id": "Worker", "to_id": "Cache",  "label": "缓存写入"}
  ],
  "subgraphs": [
    {"id": "backend", "label": "后端服务", "node_ids": ["API", "Worker"]}
  ]
}
```

━━━ ER DIAGRAM (数据模型图) ━━━
```diagram-spec
{
  "type": "erDiagram",
  "entities": [
    {
      "name": "Repository",
      "attributes": [
        {"type": "string", "name": "id",     "key": "PK"},
        {"type": "string", "name": "url"},
        {"type": "string", "name": "status"}
      ]
    },
    {
      "name": "Task",
      "attributes": [
        {"type": "string", "name": "id",      "key": "PK"},
        {"type": "string", "name": "repo_id", "key": "FK"},
        {"type": "string", "name": "type"}
      ]
    }
  ],
  "relationships": [
    {"from_entity": "Repository", "to_entity": "Task", "cardinality": "||--o{", "label": "包含"}
  ]
}
```

━━━ SEQUENCE DIAGRAM (时序图 / 调用流程) ━━━
```diagram-spec
{
  "type": "sequenceDiagram",
  "participants": [
    {"alias": "Client", "name": "客户端"},
    {"alias": "API",    "name": "FastAPI"},
    {"alias": "DB",     "name": "数据库"}
  ],
  "messages": [
    {"from_alias": "Client", "to_alias": "API", "message": "POST /api/tasks", "arrow": "->>"},
    {"from_alias": "API",    "to_alias": "DB",  "message": "INSERT Task",     "arrow": "->>"},
    {"from_alias": "DB",     "to_alias": "API", "message": "task_id",         "arrow": "-->>"},
    {"from_alias": "API",    "to_alias": "Client", "message": "202 Accepted", "arrow": "-->>"}
  ]
}
```

━━━ FIELD REFERENCE ━━━
flowchart shape (ONLY these values):
  "rect"       - rectangle (default, for services/APIs)
  "round"      - rounded rectangle (for processes)
  "diamond"    - decision node
  "stadium"    - pill shape (for workers/queues)
  "subroutine" - double-border rectangle (for databases/storage)
  FORBIDDEN shapes: "db", "database", "cylinder", "circle" — use "subroutine" or "round" instead

sequenceDiagram arrow (ONLY these values):
  "->>"  - solid arrow (request/call)
  "-->>" - dashed arrow (response/async)
  "->"   - thin solid arrow
  "-->"  - thin dashed arrow
  FORBIDDEN arrows: "<--", "<<--", "<-", "<<-"

sequenceDiagram activate/deactivate:
  AVOID using activate/deactivate unless you are certain about the pairing.
  Plain arrows (no activate/deactivate) always render correctly.
  If you must use them: activate:true on the REQUEST message (activates receiver),
  deactivate:true on the RESPONSE message FROM the activated participant back to caller.

erDiagram key (ONLY these values or omit):
  "PK" - primary key
  "FK" - foreign key
  "UK" - unique key
  FORBIDDEN: "", "Unique", "primary", "foreign" — use "PK"/"FK"/"UK" or omit the key field

erDiagram cardinality examples: "||--||", "||--o{", "}o--o{", "||--|{"

━━━ STRICT RULES ━━━
1. ALWAYS use ```diagram-spec JSON — NEVER write raw ```mermaid blocks.
2. messages array: ONLY objects with "from_alias" and "to_alias" fields.
   NEVER put loop/alt/else/note constructs inside the messages array.
3. Node/participant IDs: ASCII letters, numbers, underscores ONLY.
4. Node labels: max 30 characters.
5. Max 15 nodes per flowchart diagram.
"""

WIKI_OUTLINE_PROMPT = """You are a technical documentation expert. Analyze this code repository and create a COMPREHENSIVE, DETAILED wiki structure with 6-10 sections.

Repository: {repo_name}
Languages detected: {languages}
Language statistics: {language_stats}

Key files (by importance):
{key_files}

File structure:
{file_tree}

File summaries (classes and functions):
{file_summaries}

{readme_content_section}
Output language: {language}
All section titles, page titles, and any text content in the XML MUST be written in {language}. Do not use any other language.
IMPORTANT: A "快速上手" (Quick Start) section with non-technical overview will be automatically prepended to the Wiki by the system. Do NOT include any "Quick Start", "Getting Started", "Project Overview", or "Introduction" section. Focus entirely on technical depth.

MANDATORY REQUIREMENTS:
1. You MUST include a section covering System Architecture with an overview page (importance=high) that MUST contain a Mermaid architecture diagram showing all major modules and their relationships.
2. You MUST include a section covering Module Relationships / Dependencies showing how modules call each other, import relationships, and data flow between components.
3. You MUST include a section covering Core Data Flow / Execution Pipeline explaining the end-to-end request/processing flow with a Mermaid sequence diagram.
4. You MUST include a section for each major functional area (e.g., API layer, Service layer, Data layer, Frontend, etc.) based on the actual file structure.
5. All pages MUST reference specific source files (use relevant_files).
6. Generate between 6 and 10 sections with 2-6 pages each.

Return your analysis in the following XML format:
<wiki_structure>
  <title>[Repository name - Wiki Title]</title>
  <sections>
    <section id="section-1">
      <title>[Section title]</title>
      <pages>
        <page_ref>page-1</page_ref>
      </pages>
    </section>
  </sections>
  <pages>
    <page id="page-1">
      <title>[Page title]</title>
      <importance>high|medium|low</importance>
      <relevant_files>
        <file_path>[relative file path]</file_path>
      </relevant_files>
    </page>
  </pages>
</wiki_structure>

Focus on:
- System Architecture Overview (MANDATORY: Mermaid component/architecture diagram)
- Module interaction and dependency relationships (MANDATORY: show which modules call which)
- Core execution pipeline and data flow (MANDATORY: sequence diagram)
- All major service layers and their responsibilities
- Data models and database schema (with ERD if applicable)
- API endpoints, request/response schemas
- Configuration, environment variables, deployment
- Frontend components and state management (if applicable)
- Error handling and resilience patterns
- Testing strategy
"""

PAGE_CONTENT_PROMPT = """You are writing a detailed technical wiki page for a code repository.

Page title: {page_title}
Section: {section_title}
Repository: {repo_name}

Output language: {language}
The entire page content MUST be written in {language}. Do not use any other language.

Below is the relevant source code from the repository:

<code_context>
{code_context}
</code_context>

Write a COMPREHENSIVE, DETAILED Markdown page that:

1. **Code Location Citations (MANDATORY)**: For every function, class, or feature you describe, you MUST cite the exact location: `file_path:start_line-end_line` (e.g., `app/services/wiki_generator.py:110-216`). Never describe a function without citing where it lives.

2. **Architecture/Flow Diagrams (MANDATORY for architecture and flow pages)**: Include at least one diagram using the ```diagram-spec JSON format (see system prompt):
   - For architecture pages: `"type": "flowchart"` showing component relationships
   - For flow/pipeline pages: `"type": "sequenceDiagram"` showing call sequence
   - For data model pages: `"type": "erDiagram"` showing entity relationships

3. **Module Relationship Explanation**: Explain how this module/component interacts with OTHER modules. What does it call? What calls it? What data does it receive and produce?

4. **Implementation Details**: Explain the actual implementation logic, not just "what it does" but "HOW it does it" - algorithms, data structures, key design decisions.

5. **Code Snippets**: Include representative code snippets (with syntax highlighting) for the most important parts. Show actual code, not pseudocode.

6. **Configuration and Parameters**: Document all important configuration options, environment variables, and parameters.

7. **Error Handling**: Describe error handling patterns, fallbacks, and resilience mechanisms.

Structure your response with clear headings (## for main sections, ### for subsections).
Aim for comprehensive coverage: a reader should understand both WHAT the code does and HOW it does it.

{mermaid_constraints}
"""

# ── 三智能体协作 Prompts ────────────────────────────────────────────────────

PAGE_PLANNER_PROMPT = """Analyze the code and create a brief plan for this wiki page.

Page: {page_title}
Section: {section_title}
Repository: {repo_name}

<code_context>
{code_context}
</code_context>

Output ONLY valid JSON (no markdown fences, no explanation):
{{
  "subsections": ["title1", "title2"],
  "diagrams": [
    {{"id": "DIAGRAM_1", "type": "flowchart|erDiagram|sequenceDiagram", "description": "what to show"}}
  ],
  "key_references": ["file_path:start_line-end_line"]
}}

Rules:
- subsections: 3-6 items covering the page topic
- diagrams: 0-2 diagrams (only if genuinely useful; omit for config/testing pages)
- key_references: 3-8 most important code locations
"""

PAGE_DIAGRAM_PROMPT = """Generate ONLY the diagrams listed below. No prose, no explanations.

Repository: {repo_name}
Page: {page_title}

<code_context>
{code_context}
</code_context>

Diagrams to generate:
{diagram_specs}

For each diagram output EXACTLY (nothing else):
[DIAGRAM_N]
```diagram-spec
{{...JSON...}}
```
"""

PAGE_WRITER_PROMPT = """Write the prose content for this wiki page. Do NOT generate any diagrams.

Page title: {page_title}
Section: {section_title}
Repository: {repo_name}

Output language: {language}
The entire page MUST be written in {language}.

<code_context>
{code_context}
</code_context>

Page outline:
{outline_plan}

Where a diagram should appear, write exactly [DIAGRAM_N] on its own line.
The actual diagrams will be inserted automatically — do NOT write diagram-spec JSON.

Write a COMPREHENSIVE Markdown page:
1. **Code Citations (MANDATORY)**: Every function/class must cite `file_path:start_line-end_line`.
2. **Module Relationships**: What does this module call? What calls it?
3. **Implementation Details**: HOW it works, not just WHAT it does.
4. **Code Snippets**: Representative code with syntax highlighting.
5. **Configuration**: Important config options and environment variables.
6. **Error Handling**: Fallbacks and resilience patterns.

Use ## for main sections, ### for subsections.
"""

# ============================================================
# 快速上手页面 Prompts
# ============================================================
PAGE_SUMMARY_PROMPT = """Analyze this wiki page and write a concise 2-3 sentence summary in {language}.

Page title: {page_title}
Section: {section_title}

Content (preview):
{content_preview}

Output ONLY the summary text. No formatting, no headings, no quotes.
The summary must describe: what topics this page covers and what a reader will learn.
Keep it under 100 words."""


QUICK_START_OVERVIEW_PROMPT = """你是一位技术文档专家，请为这个项目写一个面向新用户的"项目概览"页面。

项目名称：{repo_name}
编程语言 / 技术栈：{languages}

依赖文件（含版本信息）：
{dep_context}

项目文件结构（摘要）：
{file_tree}

{readme_content_section}

输出语言：{language}
所有内容必须用 {language} 撰写。

---

要求：
1. 语言简洁友好，面向完全不了解该项目的新用户。
2. 不要引用任何代码文件路径或行号。
3. 不要生成任何图表。
4. 技术栈必须从依赖文件中提取准确版本号（如 FastAPI 0.115.x）。
5. 若 README 中有相关信息，优先提取，用自己的语言重组，不要照搬。

页面结构（严格按以下顺序，使用 ## 二级标题）：

## 这是什么项目？
（2-4 句话，说明核心用途和解决的问题）

## 主要功能
（4-10 个要点，每条 1-2 句话，从用户角度描述"我可以用它做什么"；若有子系统，用 ### 三级标题分组）

## 技术栈
（列出主要技术及版本，每项附一句"它在本项目中负责……"）

## 快速开始
根据下方提供的 README、依赖文件和 Docker 配置，按以下规则生成本节内容：

**规则 1 — Docker 部署**（若 dep_context 中存在 Dockerfile 或 docker-compose 文件内容）：
- 用 ### Docker 部署 作为子标题
- **必须解析 dep_context 中提供的实际文件内容**，提取真实的服务名、端口映射和启动命令，而非使用通用示例
  - docker-compose 场景：列出主要服务及其端口，给出准确的 `docker-compose up` 命令（含 `-d` 等常用参数）
  - 仅有 Dockerfile 场景：给出准确的 `docker build -t <镜像名> .` 和 `docker run` 命令（含端口映射 `-p`）
- 若 dep_context 中有 .env.example：列出关键环境变量并说明如何复制配置文件
- 即使 README 没有部署说明，只要有 Docker 文件内容就必须生成此节

**规则 2 — 本地部署**：
- 用 ### 本地部署 作为子标题
- 若 README 有明确步骤：直接提炼，保留原始命令并放入代码块
- 若 README 无明确步骤（或无 README）：**综合利用以下信息推断**：
  1. dep_context 中的依赖文件类型（requirements.txt → pip，package.json → npm/yarn，go.mod → go run，pom.xml → mvn，Cargo.toml → cargo）
  2. file_tree 中的入口文件（如 main.py、app.py、manage.py、index.js、server.go 等）确定启动命令
  3. 在末尾注明"⚠️ 以上步骤根据项目结构推断，具体请以项目文档为准"

**规则 3 — 格式要求**：
- 所有终端命令必须用 ``` 代码块包裹
- 步骤用有序列表（1. 2. 3.）呈现
- 若两种部署方式都有，Docker 部署放在前面

## 适合哪些用户？
（目标用户群体和使用场景，2-4 句话）"""


QUICK_START_NAVIGATION_PROMPT = """你是一位技术文档专家，请为这个 Wiki 写一个"内容导航"页面，帮助用户快速找到所需内容。

项目名称：{repo_name}

Wiki 各章节页面摘要：
{summaries_text}

输出语言：{language}
所有内容必须用 {language} 撰写。

---

请写一个 Markdown 格式的内容导航页：

1. 开篇 2-3 句话说明本 Wiki 的整体组织结构。
2. 为每个章节写一段导航说明（### 三级标题）：
   - 一句话说明该章节整体内容
   - 以"如果你想了解……，请查看**[页面标题]**"格式列出每个页面的导航提示（基于摘要，要具体）
3. 末尾加"快速定位"两列表格：| 我想了解…… | 推荐查看 |

要求：导航描述要具体（基于摘要），不要空泛；不引用代码文件；不生成图表。"""


def _get_quick_start_section(language: str) -> dict:
    """快速上手章节结构（order=0），含两个固定页面，在大纲注入时使用"""
    is_chinese = language.lower() in ("chinese", "zh", "zh-cn", "中文")
    section_title = "快速上手" if is_chinese else "Quick Start"
    return {
        "id": "section-quick-start",
        "title": section_title,
        "order": 0,
        "pages": [
            {
                "id": "page-quick-start-overview",
                "title": "项目概览" if is_chinese else "Project Overview",
                "importance": "high",
                "relevant_files": [],
                "order": 0,
                "page_type": "quick_start_overview",
            },
            {
                "id": "page-quick-start-navigation",
                "title": "内容导航" if is_chinese else "Content Navigation",
                "importance": "high",
                "relevant_files": [],
                "order": 1,
                "page_type": "quick_start_navigation",
            },
        ],
    }


async def _get_dependency_context(repo) -> str:
    """读取仓库依赖文件及部署配置，提取技术栈版本与部署信息（每个文件最多3000字符）"""
    local_path = repo.local_path if repo and repo.local_path else ""
    if not local_path:
        return "(no dependency files found)"
    candidates = [
        "requirements.txt", "requirements-dev.txt", "requirements-prod.txt",
        "pyproject.toml", "setup.py", "Pipfile",
        "frontend/package.json", "package.json",
        "go.mod", "pom.xml", "build.gradle", "Cargo.toml",
        "Dockerfile", "docker-compose.yml", "docker-compose.yaml", ".env.example",
    ]
    dep_files = []
    for fname in candidates:
        fpath = os.path.join(local_path, fname)
        if os.path.exists(fpath):
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(3000)
                dep_files.append(f"### {fname}\n```\n{content}\n```")
            except Exception:
                pass
    return "\n\n".join(dep_files) or "(no dependency files found)"


async def _generate_page_summary(
    adapter, model: str, page_title: str, section_title: str,
    content: str, language: str,
) -> str:
    """
    为单个技术页面生成 2-3 句话摘要，供内容导航页使用。
    在信号量外调用，不占并发槽。
    """
    content_preview = content[:2000]
    messages = [
        LLMMessage(role="user", content=PAGE_SUMMARY_PROMPT.format(
            page_title=page_title,
            section_title=section_title,
            content_preview=content_preview,
            language=language,
        )),
    ]
    try:
        resp = await adapter.generate_with_rate_limit(messages=messages, model=model, temperature=0.2)
        return resp.content.strip()
    except Exception as e:
        logger.warning(f"[WikiGenerator] 页面摘要生成失败: {page_title} — {e}")
        return f"{page_title}。"


async def _generate_quick_start_overview(
    adapter, model: str, repo_summary: dict, dep_context: str, language: str,
) -> str:
    """生成快速上手·项目概览页（含技术栈版本，基于依赖文件和 README）"""
    messages = [
        LLMMessage(role="user", content=QUICK_START_OVERVIEW_PROMPT.format(
            repo_name=repo_summary["repo_name"],
            languages=repo_summary["languages"],
            dep_context=dep_context,
            file_tree=repo_summary["file_tree"],
            readme_content_section=repo_summary.get("readme_content_section", ""),
            language=language,
        )),
    ]
    try:
        resp = await adapter.generate_with_rate_limit(messages=messages, model=model, temperature=0.5)
        return resp.content
    except Exception as e:
        logger.warning(f"[WikiGenerator] 项目概览页生成失败: {e}")
        is_chinese = language.lower() in ("chinese", "zh", "zh-cn", "中文")
        title = "项目概览" if is_chinese else "Project Overview"
        msg = "（页面生成失败，请稍后重试）" if is_chinese else "(Page generation failed.)"
        return f"# {title}\n\n{msg}"


async def _generate_quick_start_navigation(
    adapter, model: str, repo_name: str,
    page_summaries: List[dict], language: str,
) -> str:
    """
    生成快速上手·内容导航页。
    page_summaries: [{"section": str, "page": str, "summary": str}, ...]
    """
    is_chinese = language.lower() in ("chinese", "zh", "zh-cn", "中文")
    title = "内容导航" if is_chinese else "Content Navigation"
    if not page_summaries:
        msg = "（暂无内容）" if is_chinese else "(No content yet.)"
        return f"# {title}\n\n{msg}"
    # 按章节分组
    sections_map: Dict[str, List[dict]] = {}
    for item in page_summaries:
        sections_map.setdefault(item["section"], []).append(item)
    summaries_lines = []
    for sec_title, pages in sections_map.items():
        summaries_lines.append(f"\n**{sec_title}**")
        for p in pages:
            summaries_lines.append(f"- 《{p['page']}》：{p['summary']}")
    summaries_text = "\n".join(summaries_lines)
    messages = [
        LLMMessage(role="user", content=QUICK_START_NAVIGATION_PROMPT.format(
            repo_name=repo_name,
            summaries_text=summaries_text,
            language=language,
        )),
    ]
    try:
        resp = await adapter.generate_with_rate_limit(messages=messages, model=model, temperature=0.4)
        return resp.content
    except Exception as e:
        logger.warning(f"[WikiGenerator] 内容导航页生成失败: {e}")
        msg = "（页面生成失败，请稍后重试）" if is_chinese else "(Page generation failed.)"
        return f"# {title}\n\n{msg}"


async def _regenerate_quick_start_pages(
    db: AsyncSession,
    wiki,
    repo,
    adapter,
    model: str,
    language: str,
    collection,
    repo_summary: Optional[dict] = None,
    progress_callback: Optional[Callable] = None,
    cancel_checker: Optional[Callable] = None,
) -> None:
    """
    重新生成快速上手章节（项目概览 + 内容导航）并写入 DB。
    从 DB 读取所有技术页面的 summary 字段构建导航上下文。
    调用方：
      - generate_wiki() 末尾（首次生成）
      - update_wiki_incrementally() 末尾（每次增量同步）
      - regenerate_specific_pages() 处理快速上手页时
    """
    if cancel_checker:
        await cancel_checker()

    # 1. 获取概要（可复用已计算结果）
    if repo_summary is None:
        repo_summary = await _get_repo_summary(db, wiki.repo_id, collection)
    dep_context = await _get_dependency_context(repo)

    # 2. 从 DB 读取技术页面摘要（跳过 order_index=0 的快速上手章节）
    sections_result = await db.execute(
        select(WikiSection).where(WikiSection.wiki_id == wiki.id)
        .order_by(WikiSection.order_index)
    )
    sections = sections_result.scalars().all()

    page_summaries: List[dict] = []
    for section in sections:
        if section.order_index == 0:
            continue
        pages_result = await db.execute(
            select(WikiPage).where(WikiPage.section_id == section.id)
            .order_by(WikiPage.order_index)
        )
        tech_pages = pages_result.scalars().all()
        for page in tech_pages:
            if page.summary:
                page_summaries.append({
                    "section": section.title,
                    "page": page.title,
                    "summary": page.summary,
                })

    # 3. 并行生成两个页面
    if cancel_checker:
        await cancel_checker()
    overview_content, nav_content = await asyncio.gather(
        _generate_quick_start_overview(adapter, model, repo_summary, dep_context, language),
        _generate_quick_start_navigation(adapter, model, wiki.title, page_summaries, language),
    )

    # 4. 找到或创建快速上手章节（order_index=0）
    is_chinese = language.lower() in ("chinese", "zh", "zh-cn", "中文")
    qs_section_title = "快速上手" if is_chinese else "Quick Start"
    overview_title = "项目概览" if is_chinese else "Project Overview"
    nav_title = "内容导航" if is_chinese else "Content Navigation"

    qs_section_result = await db.execute(
        select(WikiSection).where(
            WikiSection.wiki_id == wiki.id,
            WikiSection.order_index == 0,
        )
    )
    qs_section = qs_section_result.scalars().first()
    if not qs_section:
        qs_section = WikiSection(wiki_id=wiki.id, title=qs_section_title, order_index=0)
        db.add(qs_section)
        await db.flush()

    # 5. 找到或创建两个页面（按 page_type 匹配）
    for order_idx, pg_title, content, ptype in [
        (0, overview_title, overview_content, "quick_start_overview"),
        (1, nav_title, nav_content, "quick_start_navigation"),
    ]:
        existing_result = await db.execute(
            select(WikiPage).where(
                WikiPage.section_id == qs_section.id,
                WikiPage.page_type == ptype,
            )
        )
        existing_page = existing_result.scalars().first()
        if existing_page:
            existing_page.content_md = content
        else:
            db.add(WikiPage(
                section_id=qs_section.id,
                title=pg_title,
                importance="high",
                content_md=content,
                relevant_files=[],
                order_index=order_idx,
                page_type=ptype,
                summary=None,
            ))

    await db.commit()
    logger.info(f"[WikiGenerator] 快速上手页面已更新: wiki_id={wiki.id}")


async def generate_wiki(
    db: AsyncSession,
    repo_id: str,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
    progress_callback: Optional[Callable] = None,
    cancel_checker: Optional[Callable] = None,
) -> dict:
    """
    Wiki 生成主流程。
    返回: {"wiki_id": str, "total_pages": int, "skipped_pages": int}
    """
    from app.config import settings
    adapter = create_adapter(llm_provider)
    model = llm_model or settings.DEFAULT_LLM_MODEL
    language = settings.WIKI_LANGUAGE
    collection = get_collection(repo_id)

    logger.info(f"[WikiGenerator] 开始生成 Wiki: repo_id={repo_id} provider={llm_provider} model={model}")

    # ===== 阶段 1: 生成 Wiki 大纲 =====
    if cancel_checker:
        await cancel_checker()
    if progress_callback:
        await progress_callback(10, "正在生成 Wiki 大纲...")

    repo_summary = await _get_repo_summary(db, repo_id, collection)

    outline_messages = [
        LLMMessage(role="system", content=MERMAID_CONSTRAINT_PROMPT),
        LLMMessage(role="user", content=WIKI_OUTLINE_PROMPT.format(**repo_summary, language=language)),
    ]

    outline_response = await adapter.generate_with_rate_limit(
        messages=outline_messages, model=model, temperature=0.3,
    )

    outline = _parse_wiki_outline(outline_response.content)
    logger.info(f"[WikiGenerator] 大纲解析完成: {len(outline['sections'])} 个章节")

    # 注入快速上手章节结构（order=0），其他章节 order 各加 1
    quick_start_section = _get_quick_start_section(language)
    for s in outline["sections"]:
        s["order"] += 1
    outline["sections"] = [quick_start_section] + outline["sections"]
    logger.info("[WikiGenerator] 已注入快速上手章节结构")

    # 创建 Wiki 数据库记录
    # 先检查是否已存在该仓库的 Wiki，若存在则删除旧的
    existing_wiki_result = await db.execute(
        select(Wiki).where(Wiki.repo_id == repo_id)
    )
    existing_wiki = existing_wiki_result.scalar_one_or_none()
    if existing_wiki:
        await db.delete(existing_wiki)
        await db.flush()

    wiki = Wiki(
        repo_id=repo_id,
        title=outline["title"],
        outline_json=outline,
        llm_provider=llm_provider,
        llm_model=llm_model,
    )
    db.add(wiki)
    await db.flush()

    # ===== 阶段 2: 并发生成技术页面（快速上手页在最后单独生成）=====
    technical_task_meta: List[Tuple[dict, dict]] = [
        (s_data, p_data)
        for s_data in outline["sections"]
        for p_data in s_data["pages"]
        if p_data.get("page_type") not in ("quick_start_overview", "quick_start_navigation")
    ]
    total_technical = len(technical_task_meta)

    if total_technical == 0:
        logger.warning("[WikiGenerator] 大纲中无技术页面，跳过内容生成")
        await db.flush()
        skipped_count = 0
        page_count = 0
    else:
        logger.info(
            f"[WikiGenerator] 开始生成技术页面内容: 共 {total_technical} 页"
            f"（并发数={settings.WIKI_PAGE_CONCURRENCY}）"
        )

        # 2a: 并发执行所有技术页面的 LLM 调用
        semaphore = asyncio.Semaphore(settings.WIKI_PAGE_CONCURRENCY)

        async def _generate_one(
            s_data: dict, p_data: dict
        ) -> Tuple[dict, dict, str, str]:
            if cancel_checker:
                await cancel_checker()
            async with semaphore:
                logger.info(f"[WikiGenerator] 生成页面: {p_data['title']}")
                code_context = await _retrieve_code_context(
                    collection, p_data.get("relevant_files", []), p_data["title"]
                )
                content = await _generate_page_content(
                    adapter, model, p_data, s_data["title"],
                    repo_summary["repo_name"], code_context, language,
                )
            # 摘要生成在信号量外，不占并发槽
            summary = await _generate_page_summary(
                adapter, model, p_data["title"], s_data["title"], content, language,
            )
            return s_data, p_data, content, summary

        tasks = [_generate_one(s, p) for s, p in technical_task_meta]

        if progress_callback:
            await progress_callback(50, f"并发生成 {total_technical} 个技术页面（并发数={settings.WIKI_PAGE_CONCURRENCY}）...")

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 2b: 串行写入 DB
        section_map: Dict[str, WikiSection] = {}
        page_count = 0
        skipped_count = 0

        for (s_data, p_data), result in zip(technical_task_meta, results):
            section_title = s_data["title"]

            if section_title not in section_map:
                section = WikiSection(
                    wiki_id=wiki.id,
                    title=section_title,
                    order_index=s_data["order"],
                )
                db.add(section)
                await db.flush()
                section_map[section_title] = section

            if isinstance(result, Exception):
                logger.error(
                    f"[WikiGenerator] 页面生成失败，跳过: {p_data['title']} — {result}"
                )
                skipped_count += 1
                continue

            _, _, content, summary = result
            page = WikiPage(
                section_id=section_map[section_title].id,
                title=p_data["title"],
                importance=p_data.get("importance", "medium"),
                content_md=content,
                relevant_files=p_data.get("relevant_files"),
                order_index=p_data["order"],
                summary=summary,
            )
            db.add(page)
            page_count += 1
            if progress_callback:
                pct = 50 + page_count / total_technical * 35
                await progress_callback(pct, f"写入页面 ({page_count}/{total_technical}): {p_data['title']}")
            await db.commit()

    # ===== 阶段 3: 生成快速上手页（后生成，获取完整摘要上下文）=====
    if progress_callback:
        await progress_callback(88, "正在生成快速上手页面...")

    repo = await db.get(Repository, repo_id)
    await _regenerate_quick_start_pages(
        db, wiki, repo, adapter, model, language, collection,
        repo_summary=repo_summary,
        cancel_checker=cancel_checker,
    )

    total_pages = total_technical + 2  # 含快速上手2页
    if skipped_count:
        logger.warning(
            f"[WikiGenerator] Wiki 生成完成（含跳过页）: wiki_id={wiki.id} "
            f"技术页成功={page_count} 跳过={skipped_count} 快速上手=2"
        )
    else:
        logger.info(f"[WikiGenerator] Wiki 生成完成: wiki_id={wiki.id} 共={total_pages} 页（含快速上手2页）")
    return {"wiki_id": wiki.id, "total_pages": total_pages, "skipped_pages": skipped_count}


async def regenerate_specific_pages(
    db: AsyncSession,
    repo_id: str,
    page_ids: List[str],
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
    progress_callback: Optional[Callable] = None,
    cancel_checker: Optional[Callable] = None,
) -> dict:
    """
    选择性重新生成指定页面。
    只更新 content_md，不重新生成大纲或触碰其他页面。
    返回: {"wiki_id": str, "total_pages": int, "skipped_pages": int}
    """
    from app.config import settings
    from app.models.wiki import Wiki, WikiSection, WikiPage
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    adapter = create_adapter(llm_provider)
    model = llm_model or settings.DEFAULT_LLM_MODEL
    language = settings.WIKI_LANGUAGE
    collection = get_collection(repo_id)

    logger.info(f"[WikiGenerator] 选择性重新生成 {len(page_ids)} 个页面: repo_id={repo_id}")

    # 1. 加载 Wiki（获取 wiki_id 和 repo_name）
    result = await db.execute(
        select(Wiki).where(Wiki.repo_id == repo_id).order_by(Wiki.created_at.desc()).limit(1)
    )
    wiki = result.scalar_one_or_none()
    if not wiki:
        raise ValueError(f"Wiki 不存在: repo_id={repo_id}")

    repo_name = wiki.title  # 用 wiki 标题作为 repo_name

    # 加载 repo 对象（快速上手页需要）
    repo_obj_result = await db.execute(
        select(Repository).where(Repository.id == repo_id)
    )
    repo_obj = repo_obj_result.scalars().first()

    # 2. 加载指定页面（含所属 section）
    pages_result = await db.execute(
        select(WikiPage)
        .where(WikiPage.id.in_(page_ids))
        .options(selectinload(WikiPage.section))
    )
    pages = pages_result.scalars().all()

    if not pages:
        logger.warning(f"[WikiGenerator] 未找到任何指定页面: page_ids={page_ids}")
        return {"wiki_id": wiki.id, "total_pages": 0, "skipped_pages": 0}

    # 分离快速上手页和技术页
    qs_pages = [p for p in pages if p.page_type in ("quick_start_overview", "quick_start_navigation")]
    tech_pages = [p for p in pages if p.page_type not in ("quick_start_overview", "quick_start_navigation")]

    total = len(pages)
    updated = 0
    skipped = 0

    if progress_callback:
        await progress_callback(10, f"开始重新生成 {total} 个页面...")

    # 快速上手页：统一调用专用生成函数
    if qs_pages:
        if progress_callback:
            await progress_callback(15, "重新生成快速上手页面...")
        try:
            await _regenerate_quick_start_pages(
                db, wiki, repo_obj, adapter, model, language, collection,
                cancel_checker=cancel_checker,
            )
            updated += len(qs_pages)
        except Exception as e:
            logger.error(f"[WikiGenerator] 快速上手页面重新生成失败: {e}")
            skipped += len(qs_pages)

    # 3. 并发生成技术页（复用现有逻辑）
    semaphore = asyncio.Semaphore(settings.WIKI_PAGE_CONCURRENCY)

    async def _regen_one(page: WikiPage):
        if cancel_checker:
            await cancel_checker()
        async with semaphore:
            logger.info(f"[WikiGenerator] 重新生成页面: {page.title}")
            p_data = {
                "title": page.title,
                "importance": page.importance,
                "relevant_files": page.relevant_files or [],
                "order": page.order_index,
            }
            section_title = page.section.title if page.section else ""
            code_context = await _retrieve_code_context(
                collection, p_data["relevant_files"], page.title
            )
            content = await _generate_page_content(
                adapter, model, p_data, section_title,
                repo_name, code_context, language,
            )
        summary = await _generate_page_summary(
            adapter, model, page.title, section_title, content, language,
        )
        return page, content, summary

    tasks = [_regen_one(p) for p in tech_pages]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 4. 串行写入 DB
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"[WikiGenerator] 页面重新生成失败: {result}")
            skipped += 1
            continue
        page, content, summary = result
        page.content_md = content
        page.summary = summary
        await db.commit()
        updated += 1
        if progress_callback:
            pct = 15 + (len(qs_pages) + updated) / total * 80
            await progress_callback(pct, f"已更新页面 ({updated}/{len(tech_pages)}): {page.title}")

    logger.info(f"[WikiGenerator] 选择性重新生成完成: wiki_id={wiki.id} 更新={updated} 跳过={skipped}")

    # 若有技术页被重新生成，其 summary 已更新，同步刷新快速上手导航页保持一致
    if tech_pages and updated > 0:
        if progress_callback:
            await progress_callback(97, "同步更新快速上手导航页...")
        try:
            await _regenerate_quick_start_pages(
                db, wiki, repo_obj, adapter, model, language, collection,
                cancel_checker=cancel_checker,
            )
        except Exception as e:
            logger.warning(
                f"[WikiGenerator] 快速上手导航页同步更新失败（不影响已更新的技术页）: {e}"
            )

    return {"wiki_id": wiki.id, "total_pages": updated, "skipped_pages": skipped}


async def _plan_page(
    adapter, model: str, page_data: dict,
    section_title: str, repo_name: str, code_context: str,
) -> dict:
    """Agent 1：分析代码，输出页面规划（子章节、图表需求、关键引用）"""
    messages = [
        LLMMessage(role="user", content=PAGE_PLANNER_PROMPT.format(
            page_title=page_data["title"],
            section_title=section_title,
            repo_name=repo_name,
            code_context=code_context,
        )),
    ]
    try:
        resp = await adapter.generate_with_rate_limit(messages=messages, model=model, temperature=0.2)
        raw = resp.content.strip()
        raw = re.sub(r'^```\w*\s*\n?', '', raw)
        raw = re.sub(r'\n?```\s*$', '', raw)
        return _json.loads(raw)
    except Exception as e:
        logger.warning(f"[WikiGenerator] 页面规划失败，使用默认规划: {e}")
        return {
            "subsections": ["Overview", "Implementation", "Configuration"],
            "diagrams": [{"id": "DIAGRAM_1", "type": "flowchart", "description": "Component relationships"}],
            "key_references": [],
        }


async def _generate_diagrams_only(
    adapter, model: str, page_data: dict,
    repo_name: str, code_context: str, plan: dict,
) -> str:
    """Agent 2：专注生成图表（diagram-spec JSON 块），不输出任何正文"""
    diagrams = plan.get("diagrams", [])
    if not diagrams:
        return ""
    diagram_specs = "\n".join(
        f"- {d['id']}: {d['type']} — {d['description']}" for d in diagrams
    )
    messages = [
        LLMMessage(role="system", content=MERMAID_CONSTRAINT_PROMPT),
        LLMMessage(role="user", content=PAGE_DIAGRAM_PROMPT.format(
            repo_name=repo_name,
            page_title=page_data["title"],
            code_context=code_context,
            diagram_specs=diagram_specs,
        )),
    ]
    try:
        resp = await adapter.generate_with_rate_limit(messages=messages, model=model, temperature=0.3)
        return resp.content
    except Exception as e:
        logger.warning(f"[WikiGenerator] 图表生成失败: {e}")
        return ""


async def _generate_prose_only(
    adapter, model: str, page_data: dict,
    section_title: str, repo_name: str, code_context: str,
    plan: dict, language: str,
) -> str:
    """Agent 3：专注生成正文（含 [DIAGRAM_N] 占位符），不输出任何图表"""
    subsections = plan.get("subsections", [])
    diagrams = plan.get("diagrams", [])
    outline_plan = "Subsections to cover:\n" + "\n".join(f"- {s}" for s in subsections)
    if diagrams:
        outline_plan += "\n\nDiagram placeholders:\n" + "\n".join(
            f"- Write [{d['id']}] where the {d['type']} diagram should appear" for d in diagrams
        )
    messages = [
        LLMMessage(role="user", content=PAGE_WRITER_PROMPT.format(
            page_title=page_data["title"],
            section_title=section_title,
            repo_name=repo_name,
            code_context=code_context,
            outline_plan=outline_plan,
            language=language,
        )),
    ]
    try:
        resp = await adapter.generate_with_rate_limit(messages=messages, model=model, temperature=0.5)
        return resp.content
    except Exception as e:
        logger.warning(f"[WikiGenerator] 正文生成失败: {e}")
        return ""


def _merge_diagrams_into_prose(prose: str, diagrams_raw: str) -> str:
    """将 Agent 2 输出的图表块替换到 Agent 3 正文中的 [DIAGRAM_N] 占位符"""
    if not diagrams_raw:
        # 清理未被替换的占位符
        return re.sub(r'\[DIAGRAM_\d+\]', '', prose)

    diagram_map: dict = {}
    pattern = re.compile(
        r'\[DIAGRAM_(\d+)\]\s*\n(```diagram-spec\s*\n.*?\n```)',
        re.DOTALL
    )
    for m in pattern.finditer(diagrams_raw):
        diagram_map[f"[DIAGRAM_{m.group(1)}]"] = m.group(2)

    result = prose
    for placeholder, block in diagram_map.items():
        result = result.replace(placeholder, block)
    return re.sub(r'\[DIAGRAM_\d+\]', '', result)


async def _generate_page_content(
    adapter, model: str, page_data: dict,
    section_title: str, repo_name: str, code_context: str,
    language: str = "Chinese",
) -> str:
    """
    三智能体协作生成单页内容：
      Agent 1 (Planner)  → 规划子章节与图表需求
      Agent 2 (Diagram)  → 专注生成 diagram-spec JSON（并行）
      Agent 3 (Writer)   → 专注生成正文，含 [DIAGRAM_N] 占位符（并行）
    最后合并并经 diagram-spec → Mermaid → 校验自愈流水线处理。
    """
    # Agent 1：规划（串行，为后续两个 Agent 提供上下文）
    plan = await _plan_page(adapter, model, page_data, section_title, repo_name, code_context)
    logger.info(
        f"[WikiGenerator] 页面规划完成: {page_data['title']} "
        f"| 子章节={len(plan.get('subsections', []))} "
        f"| 图表={len(plan.get('diagrams', []))}"
    )

    # Agents 2 & 3：并行执行
    diagrams_raw, prose_raw = await asyncio.gather(
        _generate_diagrams_only(adapter, model, page_data, repo_name, code_context, plan),
        _generate_prose_only(adapter, model, page_data, section_title, repo_name, code_context, plan, language),
    )

    # 合并
    content = _merge_diagrams_into_prose(prose_raw, diagrams_raw)

    # 降级兜底：两个 Agent 都失败时回退到单 Agent
    if not content.strip():
        logger.warning(f"[WikiGenerator] 三智能体均失败，降级为单 Agent: {page_data['title']}")
        messages = [
            LLMMessage(role="system", content=MERMAID_CONSTRAINT_PROMPT),
            LLMMessage(role="user", content=PAGE_CONTENT_PROMPT.format(
                page_title=page_data["title"],
                section_title=section_title,
                repo_name=repo_name,
                code_context=code_context,
                mermaid_constraints=MERMAID_CONSTRAINT_PROMPT,
                language=language,
            )),
        ]
        try:
            response = await adapter.generate_with_rate_limit(messages=messages, model=model, temperature=0.5)
            content = response.content
        except Exception as e:
            if is_token_overflow(e):
                content = await generate_with_degradation(
                    adapter, model, page_data, section_title, repo_name,
                    code_context, PAGE_CONTENT_PROMPT, MERMAID_CONSTRAINT_PROMPT,
                )
            else:
                raise

    # 后处理流水线
    content = process_diagram_specs(content)
    content = await retry_failed_diagram_specs(content, adapter, model)
    content = await validate_and_fix_mermaid(adapter, model, content)
    return content


async def _get_repo_summary(db: AsyncSession, repo_id: str, collection) -> dict:
    """
    获取仓库概要信息，用于填充 Wiki 大纲 Prompt。
    返回: {repo_name, languages, key_files, file_tree, file_summaries, language_stats}
    """
    # 获取仓库名
    repo = await db.get(Repository, repo_id)
    repo_name = repo.name if repo else repo_id

    # 从 ChromaDB 获取文件路径和语言信息
    languages = set()
    key_files = []
    file_tree = "(empty)"
    file_summaries = "(none)"
    language_stats = "unknown"

    try:
        # 获取更多 chunk 用于提取丰富的仓库信息
        results = collection.get(limit=500, include=["metadatas"])
        if results and results.get("metadatas"):
            file_counts: Dict[str, int] = {}

            # 构建文件摘要（函数/类清单）
            file_summary_map: Dict[str, dict] = {}  # file_path -> {language, functions: [], classes: []}

            for meta in results["metadatas"]:
                if meta:
                    lang = meta.get("language", "")
                    if lang:
                        languages.add(lang)
                    fp = meta.get("file_path", "")
                    if fp:
                        file_counts[fp] = file_counts.get(fp, 0) + 1
                        if fp not in file_summary_map:
                            file_summary_map[fp] = {"language": lang, "functions": [], "classes": []}
                        name = meta.get("name", "")
                        node_type = meta.get("node_type", "")
                        if name and name != "<anonymous>":
                            if "function" in node_type or "method" in node_type:
                                if name not in file_summary_map[fp]["functions"]:
                                    file_summary_map[fp]["functions"].append(name)
                            elif "class" in node_type:
                                if name not in file_summary_map[fp]["classes"]:
                                    file_summary_map[fp]["classes"].append(name)

            # 取出现次数最多的前10个文件作为关键文件
            key_files = sorted(file_counts.keys(),
                               key=lambda f: file_counts[f], reverse=True)[:10]

            # 构建文件树字符串（按目录分组）
            dirs: Dict[str, list] = {}
            for fp in sorted(file_summary_map.keys())[:50]:
                parts = fp.replace("\\", "/").split("/")
                d = "/".join(parts[:-1]) or "(root)"
                dirs.setdefault(d, []).append(parts[-1])
            file_tree_lines = []
            for d, files in sorted(dirs.items())[:20]:
                file_tree_lines.append(f"  {d}/")
                for f in files[:10]:
                    file_tree_lines.append(f"    {f}")
            file_tree = "\n".join(file_tree_lines) or "(empty)"

            # 构建文件摘要字符串（含函数/类）
            file_summaries_lines = []
            for fp, info in list(file_summary_map.items())[:20]:
                line = f"- {fp}"
                if info["classes"]:
                    line += f"  classes: {', '.join(info['classes'][:5])}"
                if info["functions"]:
                    line += f"  functions: {', '.join(info['functions'][:8])}"
                file_summaries_lines.append(line)
            file_summaries = "\n".join(file_summaries_lines) or "(none)"

            # 语言统计
            lang_counts: Dict[str, int] = {}
            for info in file_summary_map.values():
                l = info["language"]
                if l:
                    lang_counts[l] = lang_counts.get(l, 0) + 1
            language_stats = ", ".join(
                f"{l}: {c} files"
                for l, c in sorted(lang_counts.items(), key=lambda x: -x[1])[:10]
            ) or "unknown"

    except Exception as e:
        logger.warning(f"[WikiGenerator] 获取 ChromaDB 摘要失败: {e}")

    # 读取 README.md 内容（如有）
    readme_content = ""
    try:
        import os
        local_path = repo.local_path if repo and repo.local_path else ""
        if local_path:
            for readme_name in ("README.md", "readme.md", "README.rst", "README.txt"):
                readme_path = os.path.join(local_path, readme_name)
                if os.path.exists(readme_path):
                    with open(readme_path, "r", encoding="utf-8", errors="replace") as f:
                        readme_content = f.read(3000)
                    break
    except Exception:
        readme_content = ""

    return {
        "repo_name": repo_name,
        "languages": ", ".join(sorted(languages)) or "unknown",
        "key_files": "\n".join(f"- {f}" for f in key_files) or "- (no files detected)",
        "file_tree": file_tree,
        "file_summaries": file_summaries,
        "language_stats": language_stats,
        "readme_content": readme_content,
        "readme_content_section": f"## README 文档概述\n{readme_content}\n" if readme_content else "",
    }


async def _retrieve_code_context(
    collection, relevant_files: List[str], page_title: str,
    max_chunks: int = 20,
) -> str:
    """
    检索与页面相关的代码上下文。
    策略：
    1. 按 file_path 过滤（若有 relevant_files）
    2. 语义搜索（用 page_title 做 query）
    3. 多角度语义搜索补充
    4. 格式化为 code context 字符串
    """
    chunks_data = []

    try:
        # 策略 1：按文件路径过滤
        if relevant_files:
            for file_path in relevant_files[:10]:  # 最多取 10 个文件
                try:
                    results = collection.get(
                        where={"file_path": {"$eq": file_path}},
                        limit=5,
                        include=["documents", "metadatas"],
                    )
                    if results and results.get("documents"):
                        for doc, meta in zip(results["documents"], results["metadatas"]):
                            chunks_data.append((meta, doc))
                except Exception:
                    pass

        # 策略 2：语义搜索补充（若 chunks 不足）
        if len(chunks_data) < max_chunks:
            try:
                from app.services.embedder import _call_embedding_api
                query_embeddings = await _call_embedding_api([page_title])
                semantic_results = collection.query(
                    query_embeddings=query_embeddings,
                    n_results=min(max_chunks - len(chunks_data), 10),
                    include=["documents", "metadatas"],
                )
                if semantic_results and semantic_results.get("documents"):
                    for doc_list, meta_list in zip(
                        semantic_results["documents"], semantic_results["metadatas"]
                    ):
                        for doc, meta in zip(doc_list, meta_list):
                            # 去重
                            if not any(c[1] == doc for c in chunks_data):
                                chunks_data.append((meta, doc))
            except Exception as e:
                logger.warning(f"[WikiGenerator] 语义检索失败: {e}")

        # 策略 3：多角度语义搜索（用不同角度的 query 补充）
        if len(chunks_data) < max_chunks:
            alt_queries = [
                f"architecture implementation flow {page_title}",
                f"module dependency relationship {page_title}",
            ]
            for alt_q in alt_queries:
                if len(chunks_data) >= max_chunks:
                    break
                try:
                    from app.services.embedder import _call_embedding_api
                    alt_embeddings = await _call_embedding_api([alt_q])
                    alt_results = collection.query(
                        query_embeddings=alt_embeddings,
                        n_results=min(5, max_chunks - len(chunks_data)),
                        include=["documents", "metadatas"],
                    )
                    if alt_results and alt_results.get("documents"):
                        for doc_list, meta_list in zip(alt_results["documents"], alt_results["metadatas"]):
                            for doc, meta in zip(doc_list, meta_list):
                                if not any(c[1] == doc for c in chunks_data):
                                    chunks_data.append((meta, doc))
                except Exception:
                    pass

    except Exception as e:
        logger.warning(f"[WikiGenerator] 代码检索失败: {e}")

    # 格式化输出
    if not chunks_data:
        return "(No relevant code found)"

    parts = []
    for meta, doc in chunks_data[:max_chunks]:
        file_path = meta.get("file_path", "unknown") if meta else "unknown"
        name = meta.get("name", "") if meta else ""
        start_line = meta.get("start_line", 0) if meta else 0
        end_line = meta.get("end_line", 0) if meta else 0
        lang = meta.get("language", "") if meta else ""

        header = f"# {file_path}"
        if name:
            header += f" - {name}"
        if start_line and end_line:
            header += f" (lines {start_line}-{end_line})"

        fence = f"```{lang}" if lang else "```"
        parts.append(f"{header}\n{fence}\n{doc}\n```")

    return "\n\n".join(parts)


def _parse_wiki_outline(content: str) -> dict:
    """
    解析 LLM 返回的 XML 大纲。
    支持容错：提取 XML 片段，忽略前后的多余文本。

    返回结构:
    {
        "title": str,
        "sections": [
            {
                "id": str,
                "title": str,
                "order": int,
                "pages": [
                    {
                        "id": str,
                        "title": str,
                        "importance": str,
                        "relevant_files": List[str],
                        "order": int,
                    }
                ]
            }
        ]
    }
    """
    # 尝试提取 <wiki_structure>...</wiki_structure> 片段
    xml_match = re.search(
        r'<wiki_structure.*?>.*?</wiki_structure>',
        content, re.DOTALL
    )
    if not xml_match:
        logger.warning("[WikiGenerator] XML 大纲解析失败，使用默认大纲")
        return _default_outline()

    xml_text = xml_match.group(0)

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning(f"[WikiGenerator] XML 解析错误: {e}，使用默认大纲")
        return _default_outline()

    # 解析 title
    title_el = root.find("title")
    title = title_el.text.strip() if title_el is not None and title_el.text else "Wiki"

    # 解析 pages（全局 page 定义）
    pages_map: dict = {}
    pages_el = root.find("pages")
    if pages_el is not None:
        for i, page_el in enumerate(pages_el.findall("page")):
            page_id = page_el.get("id", f"page-{i + 1}")
            page_title_el = page_el.find("title")
            importance_el = page_el.find("importance")
            relevant_files_el = page_el.find("relevant_files")

            relevant_files = []
            if relevant_files_el is not None:
                for fp_el in relevant_files_el.findall("file_path"):
                    if fp_el.text:
                        relevant_files.append(fp_el.text.strip())

            pages_map[page_id] = {
                "id": page_id,
                "title": page_title_el.text.strip() if page_title_el is not None and page_title_el.text else f"Page {i + 1}",
                "importance": importance_el.text.strip() if importance_el is not None and importance_el.text else "medium",
                "relevant_files": relevant_files,
                "order": i,
            }

    # 解析 sections
    sections = []
    sections_el = root.find("sections")
    if sections_el is not None:
        for s_idx, section_el in enumerate(sections_el.findall("section")):
            section_id = section_el.get("id", f"section-{s_idx + 1}")
            s_title_el = section_el.find("title")
            section_title = s_title_el.text.strip() if s_title_el is not None and s_title_el.text else f"Section {s_idx + 1}"

            # 收集该 section 下的 pages
            section_pages = []
            pages_container = section_el.find("pages")
            if pages_container is not None:
                for p_idx, page_ref_el in enumerate(pages_container.findall("page_ref")):
                    ref_id = page_ref_el.text.strip() if page_ref_el.text else ""
                    if ref_id in pages_map:
                        page_data = dict(pages_map[ref_id])
                        page_data["order"] = p_idx
                        section_pages.append(page_data)

            # 若 section 没有 page_ref，但 pages_map 中有数据，分配一个
            if not section_pages and pages_map:
                for p_idx, (pid, pdata) in enumerate(pages_map.items()):
                    if not any(pid == p["id"] for s in sections for p in s["pages"]):
                        page_data = dict(pdata)
                        page_data["order"] = p_idx
                        section_pages.append(page_data)
                        break

            if section_pages:
                sections.append({
                    "id": section_id,
                    "title": section_title,
                    "order": s_idx,
                    "pages": section_pages,
                })

    # 若解析出的 sections 为空，使用默认结构
    if not sections:
        return _default_outline(title)

    return {
        "title": title,
        "sections": sections,
    }


def _default_outline(title: str = "Wiki") -> dict:
    """当 XML 解析失败时的兜底大纲"""
    return {
        "title": title,
        "sections": [
            {
                "id": "section-1",
                "title": "Overview",
                "order": 0,
                "pages": [
                    {
                        "id": "page-1",
                        "title": "Project Overview",
                        "importance": "high",
                        "relevant_files": [],
                        "order": 0,
                    }
                ],
            }
        ],
    }


# ============================================================
# 增量 Wiki 更新（真正的增量，仅更新受变更影响的页面）
# ============================================================

async def _suggest_section_title(
    adapter, model: str, current_title: str, page_titles: List[str], repo_name: str
) -> Optional[str]:
    """
    当一个章节的大多数页面都需要更新时，询问 LLM 是否需要更改章节标题。
    如果建议保持原标题则返回 None，否则返回新标题。
    """
    messages = [
        LLMMessage(role="user", content=(
            f"Repository: {repo_name}\n"
            f"Current section title: {current_title}\n"
            f"Pages in this section:\n" +
            "\n".join(f"- {t}" for t in page_titles) +
            "\n\nThe code in this section has been significantly updated. "
            "Does the section title still accurately describe its content? "
            "If yes, reply ONLY with the word KEEP. "
            "If the title should be updated, reply with ONLY the new title (no quotes, no explanation, max 60 chars)."
        )),
    ]
    try:
        resp = await adapter.generate_with_rate_limit(messages=messages, model=model, temperature=0.1)
        result = resp.content.strip()
        if result.upper() == "KEEP" or not result:
            return None
        # Sanity check: title must be reasonable length
        if len(result) > 80:
            return None
        return result
    except Exception as e:
        logger.warning(f"[WikiIncrUpdate] 章节标题建议失败: {e}")
        return None


async def update_wiki_incrementally(
    db: AsyncSession,
    repo_id: str,
    changed_files: List[str],
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
    progress_callback: Optional[Callable] = None,
    cancel_checker: Optional[Callable] = None,
) -> dict:
    """
    真正的增量 Wiki 更新：只更新受变更文件影响的页面，保留未受影响的内容。

    策略：
    - 通过 WikiPage.relevant_files 与 changed_files 交集找出"脏页"
    - 脏页比例 <= 65%：只更新脏页，可弹性更新章节标题
    - 脏页比例 > 65%：返回建议全量重新生成（不做任何更新）
    - 新增文件无对应页面：忽略（下次全量重新生成时会覆盖）
    - 已删除文件：从 relevant_files 中移除

    返回:
    {
        "status": "updated" | "full_regen_suggested" | "no_wiki" | "no_changes",
        "wiki_id": str | None,
        "updated_pages": int,
        "affected_ratio": float,
        "suggestion_reason": str | None,
    }
    """
    from app.models.wiki import Wiki, WikiSection, WikiPage
    from app.models.repository import Repository
    from sqlalchemy import select

    # ── 加载现有 Wiki ────────────────────────────────────────
    wiki_result = await db.execute(select(Wiki).where(Wiki.repo_id == repo_id))
    wiki = wiki_result.scalar_one_or_none()

    if not wiki:
        # 尚无 Wiki，回退到全量生成
        logger.info(f"[WikiIncrUpdate] repo_id={repo_id} 无现有 Wiki，回退全量生成")
        wiki_result = await generate_wiki(
            db, repo_id, llm_provider, llm_model, progress_callback, cancel_checker
        )
        return {"status": "no_wiki", "wiki_id": wiki_result["wiki_id"], "updated_pages": 0,
                "skipped_pages": wiki_result.get("skipped_pages", 0),
                "affected_ratio": 1.0, "suggestion_reason": None}

    # ── 加载所有 Section + Page ──────────────────────────────
    sections_result = await db.execute(
        select(WikiSection).where(WikiSection.wiki_id == wiki.id)
        .order_by(WikiSection.order_index)
    )
    sections = sections_result.scalars().all()

    section_pages_map: Dict[str, List] = {}
    all_pages = []
    for section in sections:
        pages_result = await db.execute(
            select(WikiPage).where(WikiPage.section_id == section.id)
            .order_by(WikiPage.order_index)
        )
        pages = pages_result.scalars().all()
        section_pages_map[section.id] = pages
        all_pages.extend(pages)

    if not all_pages:
        logger.info(f"[WikiIncrUpdate] repo_id={repo_id} Wiki 无页面，回退全量生成")
        wiki_result = await generate_wiki(
            db, repo_id, llm_provider, llm_model, progress_callback, cancel_checker
        )
        return {"status": "no_wiki", "wiki_id": wiki_result["wiki_id"], "updated_pages": 0,
                "skipped_pages": wiki_result.get("skipped_pages", 0),
                "affected_ratio": 1.0, "suggestion_reason": None}

    # ── 路径规范化 ───────────────────────────────────────────
    def _norm(p: str) -> str:
        return p.replace("\\", "/").lstrip("/").lower()

    changed_norm = {_norm(f) for f in changed_files if f}

    if not changed_norm:
        return {"status": "no_changes", "wiki_id": wiki.id, "updated_pages": 0,
                "affected_ratio": 0.0, "suggestion_reason": None}

    # ── 识别脏页 ─────────────────────────────────────────────
    dirty_pages = []
    clean_pages = []
    page_section_map: Dict[str, WikiSection] = {}  # page.id -> section

    for section in sections:
        for page in section_pages_map.get(section.id, []):
            # 快速上手页始终在末尾单独重新生成，不参与脏页比例计算
            if page.page_type in ("quick_start_overview", "quick_start_navigation"):
                continue
            page_section_map[page.id] = section
            relevant_norm = {_norm(f) for f in (page.relevant_files or [])}
            if relevant_norm & changed_norm:
                dirty_pages.append(page)
            else:
                clean_pages.append(page)

    affected_ratio = len(dirty_pages) / len(all_pages) if all_pages else 0.0
    logger.info(
        f"[WikiIncrUpdate] 脏页={len(dirty_pages)}/{len(all_pages)} "
        f"(比例={affected_ratio:.1%}) repo_id={repo_id}"
    )

    # ── 阈值决策 ─────────────────────────────────────────────
    FULL_REGEN_THRESHOLD = 0.65

    if affected_ratio > FULL_REGEN_THRESHOLD:
        reason = (
            f"变更影响了 {len(dirty_pages)}/{len(all_pages)} 个页面（{affected_ratio:.0%}），"
            "建议全量重新生成 Wiki 以确保内容完整性"
        )
        logger.info(f"[WikiIncrUpdate] {reason}")
        return {
            "status": "full_regen_suggested",
            "wiki_id": wiki.id,
            "updated_pages": 0,
            "affected_ratio": affected_ratio,
            "suggestion_reason": reason,
        }

    # ── 准备增量更新 ──────────────────────────────────────────
    adapter = create_adapter(llm_provider)
    model = llm_model or settings.DEFAULT_LLM_MODEL
    language = settings.WIKI_LANGUAGE
    collection = get_collection(repo_id)

    repo_obj = await db.get(Repository, repo_id)
    repo_name = repo_obj.name if repo_obj else repo_id

    if progress_callback:
        await progress_callback(5, f"增量更新：{len(dirty_pages)} 个页面受影响...")

    # 删除文件的 relevant_files 清理：延迟到下次全量重新生成处理。
    # 原因：changed_files 同时包含 A/M/D 三种类型，此处无法区分；
    # 错误移除仍存在文件的路径会导致脏页判断失效。

    # ── 并发更新脏页 ──────────────────────────────────────────
    semaphore = asyncio.Semaphore(settings.WIKI_PAGE_CONCURRENCY)

    async def _update_one(page: WikiPage) -> Tuple[WikiPage, Optional[str], Optional[str]]:
        if cancel_checker:
            await cancel_checker()
        async with semaphore:
            try:
                section = page_section_map.get(page.id)
                section_title = section.title if section else ""
                code_context = await _retrieve_code_context(
                    collection, page.relevant_files or [], page.title
                )
                new_content = await _generate_page_content(
                    adapter, model,
                    {
                        "title": page.title,
                        "importance": page.importance,
                        "relevant_files": page.relevant_files or [],
                        "order": page.order_index,
                    },
                    section_title, repo_name, code_context, language,
                )
                new_summary = await _generate_page_summary(
                    adapter, model, page.title,
                    section_title, new_content, language,
                )
                logger.info(f"[WikiIncrUpdate] 页面已更新: {page.title}")
                return page, new_content, new_summary
            except Exception as e:
                logger.error(f"[WikiIncrUpdate] 页面更新失败: {page.title} — {e}")
                return page, None, None

    tasks_list = [_update_one(p) for p in dirty_pages]
    if progress_callback:
        await progress_callback(15, f"并发更新 {len(dirty_pages)} 个受影响页面...")

    results = await asyncio.gather(*tasks_list, return_exceptions=True)

    # ── 写入 DB（串行） ───────────────────────────────────────
    updated_count = 0
    for result in results:
        if isinstance(result, Exception):
            continue
        page, new_content, new_summary = result
        if new_content is not None:
            page.content_md = new_content
            page.summary = new_summary
            updated_count += 1
    # 先提交页面内容，确保 LLM 生成的内容不因后续章节标题评估失败而丢失
    await db.commit()

    # ── 弹性更新章节标题（显著变更时） ──────────────────────────
    # IMPORTANT: _update_one 闭包中不访问 db，所有 DB 修改在 gather 完成后串行进行。
    # 若将来 _generate_page_content / _retrieve_code_context 需要访问 db，
    # 必须改为串行执行，因为 AsyncSession 不支持并发协程访问。
    SECTION_TITLE_THRESHOLD = 0.80  # 章节内 80% 以上页面为脏页时，考虑更新章节标题
    if affected_ratio > 0.20:  # 只有变更超过 20% 时才评估章节标题
        for section in sections:
            section_pages = section_pages_map.get(section.id, [])
            if not section_pages:
                continue
            dirty_in_section = [p for p in section_pages if p in dirty_pages]
            section_dirty_ratio = len(dirty_in_section) / len(section_pages)
            if section_dirty_ratio >= SECTION_TITLE_THRESHOLD:
                page_titles = [p.title for p in section_pages]
                try:
                    new_title = await _suggest_section_title(
                        adapter, model, section.title, page_titles, repo_name
                    )
                    if new_title:
                        logger.info(
                            f"[WikiIncrUpdate] 章节标题更新: "
                            f"'{section.title}' -> '{new_title}'"
                        )
                        section.title = new_title
                except Exception as e:
                    logger.warning(f"[WikiIncrUpdate] 章节标题评估失败: {e}")

    await db.commit()

    # 方案3：每次增量同步末尾都重新生成快速上手页（保障用户体验）
    if cancel_checker:
        await cancel_checker()
    if progress_callback:
        await progress_callback(96, "正在更新快速上手页面...")
    await _regenerate_quick_start_pages(
        db, wiki, repo_obj, adapter, model, language, collection,
        cancel_checker=cancel_checker,
    )

    if progress_callback:
        await progress_callback(99, f"增量更新完成：已更新 {updated_count} 个页面")

    return {
        "status": "updated",
        "wiki_id": wiki.id,
        "updated_pages": updated_count,
        "affected_ratio": affected_ratio,
        "suggestion_reason": None,
    }
