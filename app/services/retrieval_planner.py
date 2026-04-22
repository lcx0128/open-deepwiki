"""
retrieval_planner.py — 检索规划智能体

对宽泛查询（"所有 prompt"、"列举所有文件"、"翻译全部"等）使用轻量 LLM
基于代码库索引识别目标文件，实现精准的全局检索。
"""
from dataclasses import dataclass
import json
import logging
import re
from typing import List, Optional

from app.services.llm.factory import create_adapter
from app.schemas.llm import LLMMessage

logger = logging.getLogger(__name__)


@dataclass
class PlannedTarget:
    """检索规划目标：文件路径 + 可选的 symbol 名称"""

    file_path: str
    symbol_name: Optional[str] = None

# 触发规划的宽泛查询关键词（中英文）
_BROAD_PATTERNS = [
    r'所有', r'全部', r'列举', r'列出', r'找出所有', r'翻译.*所有', r'所有.*翻译',
    r'都有哪些', r'有哪些', r'全都', r'一共有',
    r'\ball\b', r'\bevery\b', r'\beach\b', r'\blist all\b', r'\btranslate all\b',
    r'\bfind all\b', r'\bshow all\b', r'\benumerate\b',
]

PLANNER_PROMPT = """\
You are a code retrieval expert. Given a codebase index and a user question, \
identify which specific files and symbols are most likely to contain the answer.

CODEBASE INDEX:
{codebase_index}

USER QUESTION: {question}

Return ONLY a JSON array of objects with "file" (required) and "symbol" (optional) fields. \
"symbol" should be the most relevant function, class, or constant name in that file. \
Return at most 5 items. Return [] if no files are clearly relevant.

Example: [
  {{"file": "app/services/chat_service.py", "symbol": "handle_chat_stream"}},
  {{"file": "app/services/wiki_generator.py", "symbol": null}}
]

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
) -> List[PlannedTarget]:
    """
    使用 LLM 规划检索目标文件。
    返回文件路径 + symbol 列表。失败时返回空列表，不影响主流程。
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
            parsed = json.loads(match.group())
            if isinstance(parsed, list):
                targets: List[PlannedTarget] = []
                for item in parsed[:5]:
                    if isinstance(item, str):
                        targets.append(PlannedTarget(file_path=item))
                    elif isinstance(item, dict) and isinstance(item.get("file"), str):
                        targets.append(
                            PlannedTarget(
                                file_path=item["file"],
                                symbol_name=item.get("symbol"),
                            )
                        )
                return targets
    except Exception as e:
        logger.warning(f"[RetrievalPlanner] 规划调用失败，跳过: {e}")
    return []
