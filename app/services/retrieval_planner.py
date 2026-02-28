"""
retrieval_planner.py — 检索规划智能体

对宽泛查询（"所有 prompt"、"列举所有文件"、"翻译全部"等）使用轻量 LLM
基于代码库索引识别目标文件，实现精准的全局检索。
"""
import json
import logging
import re
from typing import Optional

from app.services.llm.factory import create_adapter
from app.schemas.llm import LLMMessage

logger = logging.getLogger(__name__)

# 触发规划的宽泛查询关键词（中英文）
_BROAD_PATTERNS = [
    r'所有', r'全部', r'列举', r'列出', r'找出所有', r'翻译.*所有', r'所有.*翻译',
    r'都有哪些', r'有哪些', r'全都', r'一共有',
    r'\ball\b', r'\bevery\b', r'\beach\b', r'\blist all\b', r'\btranslate all\b',
    r'\bfind all\b', r'\bshow all\b', r'\benumerate\b',
]

PLANNER_PROMPT = """\
You are a code retrieval expert. Given a codebase index and a user question, \
identify which specific files are most likely to contain the answer.

CODEBASE INDEX:
{codebase_index}

USER QUESTION: {question}

Return ONLY a JSON array of file paths (relative paths as shown in the index) \
that are most relevant. Return at most 5 paths. Return [] if no files are clearly relevant.

Example: ["app/services/chat_service.py", "app/services/wiki_generator.py"]

Response (JSON array only, no explanation):"""


def is_broad_query(query: str) -> bool:
    """判断是否为需要全局规划的宽泛查询"""
    for pattern in _BROAD_PATTERNS:
        if re.search(pattern, query, re.IGNORECASE):
            return True
    return False


async def plan_retrieval(
    query: str,
    codebase_index_text: str,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
) -> list:
    """
    使用 LLM 规划检索目标文件。
    返回文件路径列表（相对路径）。失败时返回空列表，不影响主流程。
    """
    try:
        adapter = create_adapter(llm_provider)
        model = llm_model or "gpt-4o-mini"

        prompt = PLANNER_PROMPT.format(
            codebase_index=codebase_index_text[:4000],
            question=query,
        )

        response = await adapter.generate_with_rate_limit(
            messages=[LLMMessage(role="user", content=prompt)],
            model=model,
            temperature=0.1,
            max_tokens=300,
        )

        content = response.content.strip()
        # 提取 JSON 数组（容错：content 可能包含多余文字）
        match = re.search(r'\[.*?\]', content, re.DOTALL)
        if match:
            file_paths = json.loads(match.group())
            if isinstance(file_paths, list):
                return [fp for fp in file_paths if isinstance(fp, str)][:5]
    except Exception as e:
        logger.warning(f"[RetrievalPlanner] 规划调用失败，跳过: {e}")
    return []
