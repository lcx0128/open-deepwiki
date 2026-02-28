"""
模块三：Wiki 生成服务。
基于 ChromaDB 中的 ChunkNode 向量数据，通过两阶段 LLM 调用生成 Wiki 文档。
"""
import asyncio
import json as _json
import logging
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


async def generate_wiki(
    db: AsyncSession,
    repo_id: str,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
    progress_callback: Optional[Callable] = None,
    cancel_checker: Optional[Callable] = None,
) -> str:
    """
    Wiki 生成主流程。
    返回: wiki_id
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

    # ===== 阶段 2: 并发生成页面内容 =====
    total_pages = sum(len(s["pages"]) for s in outline["sections"])

    if total_pages == 0:
        logger.warning("[WikiGenerator] 大纲中无任何页面，跳过内容生成")
        await db.flush()
        return wiki.id

    logger.info(
        f"[WikiGenerator] 开始生成页面内容: 共 {total_pages} 页"
        f"（并发数={settings.WIKI_PAGE_CONCURRENCY}）"
    )

    # 2a: 并发执行所有页面的 LLM 调用（不涉及 DB，无 session 冲突）
    semaphore = asyncio.Semaphore(settings.WIKI_PAGE_CONCURRENCY)

    async def _generate_one(
        s_data: dict, p_data: dict
    ) -> Tuple[dict, dict, str]:
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
        return s_data, p_data, content

    # 保持原始顺序（section → page），asyncio.gather 保证结果顺序与任务顺序一致
    task_meta: List[Tuple[dict, dict]] = [
        (s_data, p_data)
        for s_data in outline["sections"]
        for p_data in s_data["pages"]
    ]
    tasks = [_generate_one(s, p) for s, p in task_meta]

    if progress_callback:
        await progress_callback(50, f"并发生成 {total_pages} 个页面（并发数={settings.WIKI_PAGE_CONCURRENCY}）...")

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 2b: 串行写入 DB（保持 AsyncSession 单协程使用，避免并发冲突）
    section_map: Dict[str, WikiSection] = {}
    page_count = 0

    for (s_data, p_data), result in zip(task_meta, results):
        section_title = s_data["title"]

        # 按需创建 WikiSection（每个 section 只创建一次，保持 order_index 顺序）
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
            continue

        _, _, content = result
        page = WikiPage(
            section_id=section_map[section_title].id,
            title=p_data["title"],
            importance=p_data.get("importance", "medium"),
            content_md=content,
            relevant_files=p_data.get("relevant_files"),
            order_index=p_data["order"],
        )
        db.add(page)
        page_count += 1
        if progress_callback:
            pct = 50 + page_count / total_pages * 45
            await progress_callback(pct, f"写入页面 ({page_count}/{total_pages}): {p_data['title']}")
        await db.commit()  # 每页提交一次，释放写锁，允许并发写入（如 DELETE）

    logger.info(f"[WikiGenerator] Wiki 生成完成: wiki_id={wiki.id}")
    return wiki.id


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
