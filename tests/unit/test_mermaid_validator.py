import pytest
from app.services.mermaid_validator import validate_mermaid


class TestMermaidValidator:
    def test_valid_graph_td(self):
        """有效的 graph TD 应通过校验"""
        code = "graph TD\n    A[Start] --> B[End]"
        assert validate_mermaid(code) == []

    def test_reject_graph_lr(self):
        """graph LR 必须被拒绝"""
        code = "graph LR\n    A --> B"
        errors = validate_mermaid(code)
        assert len(errors) > 0
        assert any("graph LR" in e for e in errors)

    def test_unclosed_bracket(self):
        """未闭合括号必须报错"""
        code = "graph TD\n    A[Start --> B[End]"
        errors = validate_mermaid(code)
        assert len(errors) > 0
        assert any("括号" in e for e in errors)

    def test_valid_sequence_diagram(self):
        """有效的 sequenceDiagram 应通过校验"""
        code = "sequenceDiagram\n    A->>+B: request\n    B-->>-A: response"
        assert validate_mermaid(code) == []

    def test_chinese_brackets_rejected(self):
        """中文括号必须被拒绝"""
        code = "graph TD\n    A（开始） --> B（结束）"
        errors = validate_mermaid(code)
        assert len(errors) > 0

    def test_valid_er_diagram(self):
        """有效的 erDiagram 应通过校验"""
        code = 'erDiagram\n    USER ||--o{ ORDER : places'
        assert validate_mermaid(code) == []

    def test_empty_diagram(self):
        """空代码应通过校验（无规则匹配）"""
        code = ""
        assert validate_mermaid(code) == []

    def test_mismatched_brackets(self):
        """括号不匹配应报错"""
        code = "graph TD\n    A[Start) --> B[End]"
        errors = validate_mermaid(code)
        assert len(errors) > 0
