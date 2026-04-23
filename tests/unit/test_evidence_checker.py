"""Unit tests for app/services/evidence_checker.py."""

from types import SimpleNamespace

from app.services.evidence_checker import (
    NO_RESULTS_LABEL,
    _detect_complex_aspects,
    _extract_identifiers_for_grep,
    check_evidence_sufficiency,
)


FULL_CALL_CHAIN_QUERY = "handle_chat_stream \u7684\u5b8c\u6574\u8c03\u7528\u94fe\u662f\u4ec0\u4e48"
CROSS_MODULE_QUERY = "\u8fd9\u4e2a\u529f\u80fd\u6d89\u53ca\u54ea\u4e9b\u6a21\u5757"
SIMPLE_CONSTANT_QUERY = "BUDGET_RATIO \u8fd9\u4e2a\u5e38\u91cf\u662f\u591a\u5c11"
FULL_FLOW_QUERY = "handle_chat_stream \u5b8c\u6574\u6d41\u7a0b"
CALL_CHAIN_ONLY_QUERY = "\u5b8c\u6574\u8c03\u7528\u94fe\u662f\u4ec0\u4e48"
IMPLEMENTATION_QUERY = "\u8fd9\u4e2a\u529f\u80fd\u600e\u4e48\u5b9e\u73b0\u7684"
WHY_QUERY = "\u4e3a\u4ec0\u4e48\u8fd9\u91cc\u4f1a\u5931\u8d25"
SIMPLE_RATIO_QUERY = "BUDGET_RATIO \u7684\u503c\u662f\u591a\u5c11"
WHERE_IS_CHAT_SERVICE_QUERY = "ChatService \u5728\u54ea\u91cc"
SNAKE_CASE_QUERY = "handle_chat_stream \u51fd\u6570"
QUOTED_LITERAL_QUERY = '\u627e "context_length" \u5728\u54ea'


class TestCheckEvidenceSufficiency:
    def test_empty_contents_returns_insufficient(self):
        result = check_evidence_sufficiency("any query", [], [])

        assert result.is_sufficient is False
        assert NO_RESULTS_LABEL in result.missing_aspects

    def test_few_guidelines_complex_query_returns_insufficient(self):
        guidelines = [
            SimpleNamespace(file_path="a.py", name="some_func", chunk_id="chunk-a"),
            SimpleNamespace(file_path="b.py", name="other_func", chunk_id="chunk-b"),
        ]
        contents = ["chunk-a", "chunk-b"]

        result = check_evidence_sufficiency(
            FULL_CALL_CHAIN_QUERY,
            contents,
            guidelines,
        )

        assert result.is_sufficient is False
        assert "call_chain" in result.missing_aspects

    def test_single_file_cross_module_query_returns_insufficient(self):
        guidelines = [
            SimpleNamespace(file_path="app/main.py", name="app", chunk_id=f"id-{index}")
            for index in range(5)
        ]
        contents = ["chunk"] * 5

        result = check_evidence_sufficiency(
            CROSS_MODULE_QUERY,
            contents,
            guidelines,
        )

        assert result.is_sufficient is False
        assert "cross_module" in result.missing_aspects

    def test_multi_file_contents_avoid_unnecessary_cross_module_retry(self):
        guidelines = [
            SimpleNamespace(file_path="app/main.py", name="app", chunk_id="id-1"),
            SimpleNamespace(file_path="app/main.py", name="main", chunk_id="id-2"),
        ]
        contents = [
            "// File: app/main.py (Lines 1-10)\npass",
            "// [TARGETED FILE] app/services/chat_service.py\npass",
        ]

        result = check_evidence_sufficiency(
            FULL_CALL_CHAIN_QUERY,
            contents,
            guidelines,
        )

        assert result.is_sufficient is True

    def test_sufficient_results_returns_sufficient(self):
        guidelines = [
            SimpleNamespace(file_path=f"{name}.py", name=name, chunk_id=name)
            for name in ("func_a", "func_b", "func_c", "func_d", "func_e")
        ]
        contents = ["chunk"] * 5

        result = check_evidence_sufficiency(
            SIMPLE_CONSTANT_QUERY,
            contents,
            guidelines,
        )

        assert result.is_sufficient is True

    def test_suggested_queries_contain_identifiers(self):
        result = check_evidence_sufficiency(FULL_FLOW_QUERY, [], [])
        assert "handle_chat_stream" in result.suggested_queries


class TestDetectComplexAspects:
    def test_call_chain_detected(self):
        aspects = _detect_complex_aspects(CALL_CHAIN_ONLY_QUERY)
        assert "call_chain" in aspects

    def test_implementation_detected(self):
        aspects = _detect_complex_aspects(IMPLEMENTATION_QUERY)
        assert "implementation" in aspects

    def test_why_detected(self):
        aspects = _detect_complex_aspects(WHY_QUERY)
        assert "why" in aspects

    def test_simple_query_no_aspects(self):
        aspects = _detect_complex_aspects(SIMPLE_RATIO_QUERY)
        assert aspects == []


class TestExtractIdentifiers:
    def test_camel_case(self):
        identifiers = _extract_identifiers_for_grep(WHERE_IS_CHAT_SERVICE_QUERY)
        assert "ChatService" in identifiers

    def test_snake_case(self):
        identifiers = _extract_identifiers_for_grep(SNAKE_CASE_QUERY)
        assert "handle_chat_stream" in identifiers

    def test_quoted_literal(self):
        identifiers = _extract_identifiers_for_grep(QUOTED_LITERAL_QUERY)
        assert "context_length" in identifiers

    def test_max_10_results(self):
        long_query = " ".join(f"func_{index}_name" for index in range(20))
        identifiers = _extract_identifiers_for_grep(long_query)
        assert len(identifiers) <= 10
