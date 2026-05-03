"""
retrieval_planner.py - planner for broad codebase queries.

For broad queries such as "list all prompts" or "which files are related",
use a lightweight LLM call plus the codebase index to identify targeted files
and symbols for follow-up reads.
"""

from dataclasses import dataclass
import json
import logging
import re
from typing import List, Optional

from app.schemas.llm import LLMMessage
from app.services.llm.factory import create_adapter

logger = logging.getLogger(__name__)


@dataclass
class PlannedTarget:
    """Planner output target: file path plus optional symbol."""

    file_path: str
    symbol_name: Optional[str] = None


_BROAD_PATTERNS = [
    r"所有",
    r"全部",
    r"列举",
    r"列出",
    r"找出所有",
    r"翻译.*所有",
    r"所有.*翻译",
    r"都有哪",
    r"有哪些",
    r"全都",
    r"一共",
    r"\ball\b",
    r"\bevery\b",
    r"\beach\b",
    r"\blist all\b",
    r"\btranslate all\b",
    r"\bfind all\b",
    r"\bshow all\b",
    r"\benumerate\b",
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
    """Return whether the query should trigger planner-based retrieval."""
    for pattern in _BROAD_PATTERNS:
        if re.search(pattern, query, re.IGNORECASE):
            logger.debug(
                "[RetrievalPlanner] broad query detected: pattern=%r, query=%r",
                pattern,
                query[:120],
            )
            return True

    logger.debug("[RetrievalPlanner] broad query not detected: query=%r", query[:120])
    return False


async def plan_retrieval(
    query: str,
    codebase_index_text: str,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
) -> List[PlannedTarget]:
    """
    Use an LLM to plan targeted file reads.
    Returns an empty list on failure so the main pipeline can continue.
    """
    try:
        adapter = create_adapter(llm_provider)
        model = llm_model or "gpt-4o-mini"

        prompt = PLANNER_PROMPT.format(
            codebase_index=codebase_index_text[:4000],
            question=query,
        )
        logger.debug(
            "[RetrievalPlanner] start: model=%s, query=%r, index_chars=%s",
            model,
            query[:120],
            len(codebase_index_text),
        )

        response = await adapter.generate_with_rate_limit(
            messages=[LLMMessage(role="user", content=prompt)],
            model=model,
            temperature=0.1,
            max_tokens=300,
        )

        content = response.content.strip()
        match = re.search(r"\[.*?\]", content, re.DOTALL)
        if not match:
            logger.debug(
                "[RetrievalPlanner] no JSON array parsed from response: %r",
                content[:300],
            )
            return []

        parsed = json.loads(match.group())
        if not isinstance(parsed, list):
            logger.debug(
                "[RetrievalPlanner] parsed payload is not a list: %r",
                type(parsed).__name__,
            )
            return []

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

        logger.debug(
            "[RetrievalPlanner] planned targets: %s",
            [
                {"file": target.file_path, "symbol": target.symbol_name}
                for target in targets
            ],
        )
        return targets
    except Exception as exc:
        logger.warning(f"[RetrievalPlanner] 规划调用失败，跳过: {exc}")
        return []
