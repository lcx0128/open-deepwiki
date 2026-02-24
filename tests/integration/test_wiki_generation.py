import pytest
from app.services.wiki_generator import _parse_wiki_outline, _default_outline


class TestWikiOutlineParsing:
    def test_parse_valid_xml(self):
        """解析标准 XML 大纲"""
        xml_content = '''
<wiki_structure>
  <title>Test Wiki</title>
  <sections>
    <section id="s-1">
      <title>Overview</title>
      <pages>
        <page_ref>p-1</page_ref>
      </pages>
    </section>
  </sections>
  <pages>
    <page id="p-1">
      <title>Architecture</title>
      <importance>high</importance>
      <relevant_files>
        <file_path>src/main.py</file_path>
        <file_path>src/config.py</file_path>
      </relevant_files>
    </page>
  </pages>
</wiki_structure>'''
        outline = _parse_wiki_outline(xml_content)
        assert outline["title"] == "Test Wiki"
        assert len(outline["sections"]) == 1
        assert outline["sections"][0]["title"] == "Overview"
        assert len(outline["sections"][0]["pages"]) == 1
        assert outline["sections"][0]["pages"][0]["title"] == "Architecture"
        assert outline["sections"][0]["pages"][0]["importance"] == "high"
        assert "src/main.py" in outline["sections"][0]["pages"][0]["relevant_files"]

    def test_parse_malformed_xml_fallback(self):
        """LLM 可能返回非完美 XML，需要容错"""
        bad_xml = "Some preamble\n<wiki_structure><title>Test</title></wiki_structure>\nExtra text"
        outline = _parse_wiki_outline(bad_xml)
        assert outline["title"] == "Test"

    def test_parse_empty_content_fallback(self):
        """空内容时使用默认大纲"""
        outline = _parse_wiki_outline("")
        assert "title" in outline
        assert "sections" in outline
        assert len(outline["sections"]) > 0

    def test_parse_multi_section_xml(self):
        """多 section 解析"""
        xml_content = '''
<wiki_structure>
  <title>Multi Section Wiki</title>
  <sections>
    <section id="s-1">
      <title>Section One</title>
      <pages><page_ref>p-1</page_ref></pages>
    </section>
    <section id="s-2">
      <title>Section Two</title>
      <pages><page_ref>p-2</page_ref></pages>
    </section>
  </sections>
  <pages>
    <page id="p-1">
      <title>Page One</title>
      <importance>high</importance>
      <relevant_files></relevant_files>
    </page>
    <page id="p-2">
      <title>Page Two</title>
      <importance>medium</importance>
      <relevant_files></relevant_files>
    </page>
  </pages>
</wiki_structure>'''
        outline = _parse_wiki_outline(xml_content)
        assert len(outline["sections"]) == 2
        assert outline["sections"][0]["title"] == "Section One"
        assert outline["sections"][1]["title"] == "Section Two"

    def test_default_outline_structure(self):
        """默认大纲结构完整"""
        outline = _default_outline("My Project")
        assert outline["title"] == "My Project"
        assert len(outline["sections"]) >= 1
        assert len(outline["sections"][0]["pages"]) >= 1


class TestTokenDegradation:
    def test_is_token_overflow_detection(self):
        """Token 溢出检测"""
        from app.services.token_degradation import is_token_overflow

        class FakeError(Exception):
            pass

        assert is_token_overflow(FakeError("context_length_exceeded"))
        assert is_token_overflow(FakeError("maximum context length reached"))
        assert is_token_overflow(FakeError("token limit exceeded"))
        assert not is_token_overflow(FakeError("some other error"))
        assert not is_token_overflow(FakeError("network timeout"))
