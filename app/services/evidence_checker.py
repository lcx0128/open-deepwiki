"""Evidence sufficiency checks for chat retrieval."""

import logging
import re
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)


NO_RESULTS_LABEL = "\u65e0\u68c0\u7d22\u7ed3\u679c"

_ZH_CALL_CHAIN = [
    "\u8c03\u7528\u94fe",
    "\u8c03\u7528\u5173\u7cfb",
    "\u6267\u884c\u6d41\u7a0b",
    "\u5b8c\u6574\u6d41\u7a0b",
    "\u6267\u884c\u8def\u5f84",
    "\u4ece.*\u5230",
    "\u5168\u94fe\u8def",
    "\u6570\u636e\u6d41",
    "\u8bf7\u6c42\u94fe\u8def",
]
_ZH_IMPLEMENTATION = [
    "\u600e\u4e48\u5b9e\u73b0",
    "\u5982\u4f55\u5b9e\u73b0",
    "\u5b9e\u73b0\u539f\u7406",
    "\u5b9e\u73b0\u903b\u8f91",
    "\u5b9e\u73b0\u7ec6\u8282",
    "\u5e95\u5c42.*\u5b9e\u73b0",
    "\u6838\u5fc3.*\u5b9e\u73b0",
    "\u6e90\u7801.*\u5206\u6790",
]
_ZH_WHY = [
    "\u4e3a\u4ec0\u4e48",
    "\u4e3a\u4f55",
    "\u539f\u56e0\u662f",
    "\u4ec0\u4e48\u539f\u56e0",
]
_ZH_CROSS_MODULE = [
    "\u8de8.*\u6a21\u5757",
    "\u591a\u4e2a.*\u6587\u4ef6",
    "\u54ea\u4e9b.*\u6a21\u5757",
    "\u5173\u8054.*\u6587\u4ef6",
    "\u6240\u6709.*\u76f8\u5173",
    "\u6d89\u53ca.*\u54ea\u4e9b",
]


@dataclass
class EvidenceCheckResult:
    """Result of a lightweight evidence sufficiency check."""

    is_sufficient: bool
    missing_aspects: List[str] = field(default_factory=list)
    suggested_queries: List[str] = field(default_factory=list)


_COMPLEX_PATTERNS = {
    "call_chain": [
        *_ZH_CALL_CHAIN,
        r"call chain",
        r"call graph",
        r"execution flow",
        r"full flow",
        r"end.to.end",
        r"data flow",
        r"request path",
    ],
    "implementation": [
        *_ZH_IMPLEMENTATION,
        r"how.*implement",
        r"implementation",
        r"how.*work",
    ],
    "why": [
        *_ZH_WHY,
        r"why",
        r"reason",
        r"root cause",
    ],
    "cross_module": [
        *_ZH_CROSS_MODULE,
        r"across.*module",
        r"which.*files",
        r"related.*modules",
    ],
}

_FILE_HEADER_PATTERNS = [
    re.compile(r"^// File: (?P<file_path>.+?) \(Lines \d+-\d+\)", re.MULTILINE),
    re.compile(r"^// \[TARGETED FILE\] (?P<file_path>[^\r\n(]+)", re.MULTILINE),
]


def check_evidence_sufficiency(
    query: str,
    code_contents: List[str],
    guidelines: list,
) -> EvidenceCheckResult:
    """Judge whether the current retrieved evidence is enough to answer."""

    if not code_contents:
        result = EvidenceCheckResult(
            is_sufficient=False,
            missing_aspects=[NO_RESULTS_LABEL],
            suggested_queries=_extract_identifiers_for_grep(query),
        )
        logger.debug(
            "[EvidenceCheck] insufficient: no code contents, query=%r, suggested=%s",
            query[:120],
            result.suggested_queries[:5],
        )
        return result

    detected_aspects = _detect_complex_aspects(query)
    query_is_complex = bool(detected_aspects)

    guideline_hit_files = {
        file_path
        for guideline in guidelines
        if (file_path := getattr(guideline, "file_path", ""))
    }
    content_hit_files = _extract_file_paths_from_contents(code_contents)
    hit_files = guideline_hit_files | content_hit_files
    has_additional_content_files = bool(content_hit_files - guideline_hit_files)

    if len(guidelines) < 3 and query_is_complex and not has_additional_content_files:
        result = EvidenceCheckResult(
            is_sufficient=False,
            missing_aspects=detected_aspects,
            suggested_queries=_extract_identifiers_for_grep(query),
        )
        logger.debug(
            "[EvidenceCheck] insufficient: low_guideline_count=%s, aspects=%s, hit_files=%s, suggested=%s",
            len(guidelines),
            detected_aspects,
            sorted(hit_files),
            result.suggested_queries[:5],
        )
        return result

    is_cross_module = (
        "cross_module" in detected_aspects or "call_chain" in detected_aspects
    )
    if len(hit_files) <= 1 and is_cross_module:
        suggested_queries = _extract_identifiers_for_grep(query)
        for guideline in guidelines[:5]:
            name = getattr(guideline, "name", "")
            if name and name not in suggested_queries:
                suggested_queries.append(name)

        result = EvidenceCheckResult(
            is_sufficient=False,
            missing_aspects=detected_aspects,
            suggested_queries=suggested_queries[:10],
        )
        logger.debug(
            "[EvidenceCheck] insufficient: cross_module_single_file, aspects=%s, hit_files=%s, suggested=%s",
            detected_aspects,
            sorted(hit_files),
            result.suggested_queries[:5],
        )
        return result

    if len(guidelines) >= 5 and not query_is_complex:
        result = EvidenceCheckResult(is_sufficient=True)
        logger.debug(
            "[EvidenceCheck] sufficient: simple_query guideline_count=%s hit_files=%s",
            len(guidelines),
            sorted(hit_files),
        )
        return result

    result = EvidenceCheckResult(is_sufficient=True)
    logger.debug(
        "[EvidenceCheck] sufficient: default path, aspects=%s, guideline_count=%s, hit_files=%s, extra_content_files=%s",
        detected_aspects,
        len(guidelines),
        sorted(hit_files),
        has_additional_content_files,
    )
    return result


def _detect_complex_aspects(query: str) -> List[str]:
    """Detect whether the query implies multi-hop or cross-file evidence."""

    detected = []
    for aspect, patterns in _COMPLEX_PATTERNS.items():
        if any(re.search(pattern, query, re.IGNORECASE) for pattern in patterns):
            detected.append(aspect)
    return detected


def _extract_identifiers_for_grep(query: str) -> List[str]:
    """Extract conservative identifier candidates for supplemental grep."""

    identifiers = []
    identifiers.extend(
        re.findall(r"\b([A-Z][a-z]+(?:[A-Z][a-z0-9]+)+)\b", query)
    )
    identifiers.extend(
        re.findall(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b", query)
    )
    identifiers.extend(
        re.findall(r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\b", query)
    )
    identifiers.extend(re.findall(r"[\"']([^\"']{3,})[\"']", query))

    unique_identifiers = []
    seen = set()
    for identifier in identifiers:
        if identifier in seen:
            continue
        seen.add(identifier)
        unique_identifiers.append(identifier)

    return unique_identifiers[:10]


def _extract_file_paths_from_contents(code_contents: List[str]) -> set[str]:
    """Extract file paths from stage2/gap-fill/planned-content headers."""

    file_paths = set()
    for content in code_contents:
        for pattern in _FILE_HEADER_PATTERNS:
            match = pattern.search(content)
            if not match:
                continue
            file_paths.add(match.group("file_path").strip())
            break
    return file_paths
