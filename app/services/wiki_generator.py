"""
模块三：Wiki 生成服务。
基于 ChromaDB 中的 ChunkNode 向量数据，通过两阶段 LLM 调用生成 Wiki 文档。
"""
import logging
import re
import xml.etree.ElementTree as ET
from typing import List, Optional, Callable, Dict

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.services.llm.factory import create_adapter
from app.services.embedder import get_collection
from app.schemas.llm import LLMMessage
from app.models.wiki import Wiki, WikiSection, WikiPage
from app.models.repository import Repository
from app.services.mermaid_validator import validate_and_fix_mermaid
from app.services.token_degradation import is_token_overflow, generate_with_degradation

logger = logging.getLogger(__name__)

# ============================================================
# Mermaid 约束 System Prompt
# ============================================================
MERMAID_CONSTRAINT_PROMPT = """
=== CRITICAL MERMAID SYNTAX RULES — MUST FOLLOW ALL OR DIAGRAM WILL BREAK ===

━━━ RULE 1: FLOW DIAGRAMS — ALWAYS `flowchart TD`, NEVER `graph LR` / `graph TD` ━━━
WRONG:   graph LR
         A["Start"] --> B["End"]
WRONG:   graph TD
         A["Start"] --> B["End"]
CORRECT: flowchart TD
         A["Start"] --> B["End"]
         subgraph Layer["层级"]
             C["服务"]
         end

━━━ RULE 2: NODE IDs MUST BE ASCII-ONLY (CRITICAL for Mermaid 10.x) ━━━
Node IDs (before [ ]) MUST be ASCII letters, numbers, or underscores only.
Chinese/non-ASCII text MUST ONLY appear INSIDE double-quoted label brackets.

WRONG (causes "Syntax error in text"):
    客户端 --> API网关 --> 服务层
    客户端[Client] --> API网关[Gateway]

CORRECT:
    A["客户端"] --> B["API网关"] --> C["服务层"]
    Client["客户端"] --> Gateway["API网关"]

━━━ RULE 3: ALL NODE LABELS MUST USE DOUBLE QUOTES (CRITICAL) ━━━
Every node label MUST be wrapped in double quotes to prevent special-character parse errors.
NEVER put double quotes inside the label text itself.

WRONG:   A[客户端] --> B[API网关]
WRONG:   A[Function(args)] --> B[key:value]
CORRECT: A["客户端"] --> B["API网关"]
CORRECT: A["Function args"] --> B["key value"]

━━━ RULE 4: erDiagram — CHINESE LABELS MUST USE DOUBLE QUOTES ━━━
In erDiagram, relationship labels after the colon that contain Chinese MUST be in double quotes.

WRONG (causes 'Expecting ALPHANUM got 中'):
    Project ||--o{ Wiki : 拥有
CORRECT:
    Project ||--o{ Wiki : "拥有"
    User ||--o{ Task : "创建"

━━━ RULE 5: SEQUENCE DIAGRAMS ━━━
    - First line: sequenceDiagram (on its own line)
    - ALWAYS declare participants first with aliases:
        participant A as 客户端
        participant B as API网关
    - Sync call:   A->>B: message
    - Async return: B-->>A: response
    - Activation:  A->>+B: request  then  B-->>-A: response
    - Note:        Note over A,B: message text

━━━ RULE 6: NO HTML TAGS INSIDE NODES ━━━
WRONG:   A["<b>Title</b>"] or A["<br/>line"]
CORRECT: A["Title"]

━━━ RULE 7: KEEP NODE LABELS SHORT — MAX 30 CHARACTERS ━━━
WRONG:   A["这是一个描述非常详细超过三十个字符的服务组件名称"]
CORRECT: A["详细服务组件"]

━━━ RULE 8: MAXIMUM 20 NODES PER DIAGRAM ━━━
Split large diagrams into multiple smaller focused ones.

━━━ RULE 9: NO UNMATCHED BRACKETS ━━━
Every [ must have ], every ( must have ).
WRONG:   A["客户端 --> B["API"]
CORRECT: A["客户端"] --> B["API"]

━━━ RULE 10: NO SEMICOLONS AS LINE SEPARATORS ━━━
Each statement must be on its own line.
WRONG:   A --> B; B --> C
CORRECT:
    A --> B
    B --> C

=== PRE-GENERATION CHECKLIST (verify before outputting each diagram) ===
□ Using flowchart TD (not graph TD, not graph LR)?
□ All node IDs are ASCII-only? (no Chinese as node IDs)
□ All node labels wrapped in double quotes?
□ All erDiagram Chinese relationship labels wrapped in double quotes?
□ All brackets matched?
□ All node labels under 30 characters?
□ Fewer than 20 nodes total?
□ No HTML tags inside node labels?
□ Sequence diagrams declare participants first?
□ All subgraphs closed with `end`?
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

2. **Architecture/Flow Diagrams (MANDATORY for architecture and flow pages)**: Include at least one Mermaid diagram showing:
   - For architecture pages: component relationships using `graph TD`
   - For flow/pipeline pages: sequence diagrams using `sequenceDiagram`
   - For data model pages: ER diagrams using `erDiagram`

3. **Module Relationship Explanation**: Explain how this module/component interacts with OTHER modules. What does it call? What calls it? What data does it receive and produce?

4. **Implementation Details**: Explain the actual implementation logic, not just "what it does" but "HOW it does it" - algorithms, data structures, key design decisions.

5. **Code Snippets**: Include representative code snippets (with syntax highlighting) for the most important parts. Show actual code, not pseudocode.

6. **Configuration and Parameters**: Document all important configuration options, environment variables, and parameters.

7. **Error Handling**: Describe error handling patterns, fallbacks, and resilience mechanisms.

Structure your response with clear headings (## for main sections, ### for subsections).
Aim for comprehensive coverage: a reader should understand both WHAT the code does and HOW it does it.

{mermaid_constraints}
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

    # ===== 阶段 2: 逐页生成内容 =====
    total_pages = sum(len(s["pages"]) for s in outline["sections"])
    page_count = 0

    if total_pages == 0:
        logger.warning("[WikiGenerator] 大纲中无任何页面，跳过内容生成")
        await db.flush()
        return wiki.id

    logger.info(f"[WikiGenerator] 开始生成页面内容: 共 {total_pages} 页")

    for section_data in outline["sections"]:
        section = WikiSection(
            wiki_id=wiki.id,
            title=section_data["title"],
            order_index=section_data["order"],
        )
        db.add(section)
        await db.flush()

        for page_data in section_data["pages"]:
            page_count += 1
            if progress_callback:
                pct = page_count / total_pages * 100
                await progress_callback(pct, f"生成页面 ({page_count}/{total_pages}): {page_data['title']}")

            if cancel_checker:
                await cancel_checker()
            logger.info(f"[WikiGenerator] 生成页面: {page_data['title']}")

            code_context = await _retrieve_code_context(
                collection, page_data.get("relevant_files", []), page_data["title"]
            )

            content = await _generate_page_content(
                adapter, model, page_data, section_data["title"],
                repo_summary["repo_name"], code_context, language,
            )

            page = WikiPage(
                section_id=section.id,
                title=page_data["title"],
                importance=page_data.get("importance", "medium"),
                content_md=content,
                relevant_files=page_data.get("relevant_files"),
                order_index=page_data["order"],
            )
            db.add(page)
            await db.commit()  # 每页提交一次，释放写锁，允许并发写入（如 DELETE）

    logger.info(f"[WikiGenerator] Wiki 生成完成: wiki_id={wiki.id}")
    return wiki.id


async def _generate_page_content(
    adapter, model: str, page_data: dict,
    section_title: str, repo_name: str, code_context: str,
    language: str = "Chinese",
) -> str:
    """生成单页内容并校验 Mermaid"""
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
        response = await adapter.generate_with_rate_limit(
            messages=messages, model=model, temperature=0.5,
        )
        content = response.content
    except Exception as e:
        if is_token_overflow(e):
            logger.warning(f"[WikiGenerator] Token 溢出，启动降级策略: {page_data['title']}")
            content = await generate_with_degradation(
                adapter, model, page_data, section_title, repo_name,
                code_context, PAGE_CONTENT_PROMPT, MERMAID_CONSTRAINT_PROMPT,
            )
        else:
            raise

    # Mermaid 校验与自愈
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

    return {
        "repo_name": repo_name,
        "languages": ", ".join(sorted(languages)) or "unknown",
        "key_files": "\n".join(f"- {f}" for f in key_files) or "- (no files detected)",
        "file_tree": file_tree,
        "file_summaries": file_summaries,
        "language_stats": language_stats,
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
