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
When generating Mermaid diagrams, you MUST follow these rules strictly:

1. Flow/architecture diagrams: ALWAYS use `graph TD` (top-down). NEVER use `graph LR`.
2. Sequence diagrams:
   - Start with `sequenceDiagram` on its own line
   - Synchronous call: `A->>B: message`
   - Async return: `B-->>A: response`
   - Async fire-and-forget: `A-)B: event`
   - Activation: `A->>+B: request` (activate B), `B-->>-A: response` (deactivate B)
3. ER diagrams:
   - Use `erDiagram` keyword
   - Cardinality: `||--o{` (one-to-many), `||--||` (one-to-one), `}o--o{` (many-to-many)
4. NEVER use HTML tags inside Mermaid nodes
5. NEVER use special characters (parentheses, quotes) in node labels without quoting
6. Keep node labels short (max 30 characters)
7. Maximum 20 nodes per diagram to prevent overflow
"""

WIKI_OUTLINE_PROMPT = """You are a technical documentation expert. Analyze this code repository and create a comprehensive wiki structure.

Repository: {repo_name}
Languages detected: {languages}
Key files: {key_files}

Output language: {language}
All section titles, page titles, and any text content in the XML MUST be written in {language}. Do not use any other language.

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

Generate 3-8 sections with 2-5 pages each. Focus on:
- Architecture overview (high importance)
- Core modules and their interactions
- Data models and database schema
- API endpoints and usage
- Configuration and deployment
"""

PAGE_CONTENT_PROMPT = """You are writing a detailed technical wiki page.

Page title: {page_title}
Section: {section_title}
Repository: {repo_name}

Output language: {language}
The entire page content MUST be written in {language}. Do not use any other language.

Below is the relevant source code from the repository:

<code_context>
{code_context}
</code_context>

Write a comprehensive, well-structured Markdown page that:
1. Explains the purpose and functionality of the code
2. Includes code snippets where helpful (use proper syntax highlighting)
3. Includes Mermaid diagrams where appropriate to visualize architecture or flow
4. References specific file paths and line numbers
5. Is written in a clear, professional tone

{mermaid_constraints}
"""


async def generate_wiki(
    db: AsyncSession,
    repo_id: str,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
    progress_callback: Optional[Callable] = None,
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

    await db.flush()
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
    返回: {repo_name, languages, key_files}
    """
    # 获取仓库名
    repo = await db.get(Repository, repo_id)
    repo_name = repo.name if repo else repo_id

    # 从 ChromaDB 获取文件路径和语言信息
    languages = set()
    key_files = []

    try:
        # 获取少量 chunk 用于提取语言和关键文件信息
        results = collection.get(limit=200, include=["metadatas"])
        if results and results.get("metadatas"):
            file_counts: Dict[str, int] = {}
            for meta in results["metadatas"]:
                if meta:
                    lang = meta.get("language", "")
                    if lang:
                        languages.add(lang)
                    file_path = meta.get("file_path", "")
                    if file_path:
                        file_counts[file_path] = file_counts.get(file_path, 0) + 1

            # 取出现次数最多的前10个文件作为关键文件
            key_files = sorted(file_counts.keys(),
                               key=lambda f: file_counts[f], reverse=True)[:10]
    except Exception as e:
        logger.warning(f"[WikiGenerator] 获取 ChromaDB 摘要失败: {e}")

    return {
        "repo_name": repo_name,
        "languages": ", ".join(sorted(languages)) or "unknown",
        "key_files": "\n".join(f"- {f}" for f in key_files) or "- (no files detected)",
    }


async def _retrieve_code_context(
    collection, relevant_files: List[str], page_title: str,
    max_chunks: int = 10,
) -> str:
    """
    检索与页面相关的代码上下文。
    策略：
    1. 按 file_path 过滤（若有 relevant_files）
    2. 语义搜索（用 page_title 做 query）
    3. 格式化为 code context 字符串
    """
    chunks_data = []

    try:
        # 策略 1：按文件路径过滤
        if relevant_files:
            for file_path in relevant_files[:5]:  # 最多取 5 个文件
                try:
                    results = collection.get(
                        where={"file_path": {"$eq": file_path}},
                        limit=3,
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
                    n_results=min(max_chunks - len(chunks_data), 5),
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
