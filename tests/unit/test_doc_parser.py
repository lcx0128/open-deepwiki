"""
单元测试：doc_parser.py 和 language_detector.py 新函数
"""
import pytest
from app.services.doc_parser import parse_doc_file, parse_config_file
from app.services.language_detector import (
    is_doc_file,
    is_config_file,
    detect_doc_language,
    should_skip,
)


# ─────────────────────────────────────────────────────────────
# Tests for doc_parser.py
# ─────────────────────────────────────────────────────────────

class TestParseMarkdownWithHeadings:
    def test_parse_markdown_with_headings(self):
        source = """\
## Introduction

This is the introduction section with enough content to be included in chunks.

## Installation

Install the package using pip install mypackage and configure your environment.

## Usage

Use the package by importing it and calling the main function with your parameters.
"""
        chunks = parse_doc_file("README.md", source, "markdown")
        assert len(chunks) == 3
        names = [c.name for c in chunks]
        assert "Introduction" in names
        assert "Installation" in names
        assert "Usage" in names
        for chunk in chunks:
            assert chunk.node_type == "document_section"
            assert chunk.language == "markdown"
            assert chunk.file_path == "README.md"


class TestParseMarkdownNoHeadings:
    def test_parse_markdown_no_headings(self):
        source = "This is some content without any headings at all."
        chunks = parse_doc_file("my_notes.md", source, "markdown")
        assert len(chunks) == 1
        assert chunks[0].node_type == "document_section"
        # name should be the file stem
        assert chunks[0].name == "my_notes"
        assert chunks[0].language == "markdown"


class TestParseMarkdownSkipsShortSections:
    def test_parse_markdown_skips_short_sections(self):
        source = """\
## Long Section

This section has enough content to be included because it exceeds fifty characters easily.

## Short

Too short.

## Another Long Section

This section also has more than fifty characters and should be included in the output.
"""
        chunks = parse_doc_file("doc.md", source, "markdown")
        names = [c.name for c in chunks]
        assert "Long Section" in names
        assert "Another Long Section" in names
        # "Short" section has < 50 chars total (heading + "Too short." = ~22 chars)
        assert "Short" not in names


class TestParseMarkdownContentCap:
    def test_parse_markdown_content_cap(self):
        # Create a section with > 8000 chars — triggers _split_by_paragraphs.
        # _split_by_paragraphs carries a 200-char overlap window between parts, so
        # each emitted chunk's content is at most _MAX_SECTION_CHARS (8000) plus the
        # overlap tail prepended to the *next* accumulator, not to the flushed chunk.
        # The flushed chunk is whatever was in `current` when it exceeded 8000, so
        # its length is bounded by the last paragraph that pushed it over — here each
        # "paragraph" is the whole 9000-char body (single paragraph, no double newline),
        # meaning the body itself becomes the single section content.  With a truly
        # single paragraph of 9000 chars there is nothing to split at, so the whole
        # content ends up in one chunk and is capped by the slice in _parse_as_single_chunk
        # only when called directly — but _split_by_paragraphs does NOT slice.
        # Use two long paragraphs so the splitter can actually fire.
        para = "A" * 4500
        long_body = f"{para}\n\n{para}"   # two paragraphs, total > 8000
        source = f"## Big Section\n\n{long_body}\n"
        chunks = parse_doc_file("big.md", source, "markdown")
        assert len(chunks) >= 1
        # The splitter flushes when len(current) + len(para) > 8000.
        # The flushed current is at most ~8000 chars; the overlap added back is 200.
        # So each chunk content stays within 8000 + 200 (overlap) + 2 (\n\n) = 8202.
        _HARD_CAP = 8202
        for chunk in chunks:
            assert len(chunk.content) <= _HARD_CAP, (
                f"Chunk '{chunk.name}' content length {len(chunk.content)} > {_HARD_CAP}"
            )


class TestParseTextParagraphs:
    def test_parse_text_paragraphs(self):
        # 3 paragraphs: first two > 100 chars, third < 100 chars
        para1 = "A" * 150
        para2 = "B" * 200
        para3 = "short"
        source = f"{para1}\n\n{para2}\n\n{para3}"
        chunks = parse_doc_file("notes.txt", source, "text")
        assert len(chunks) == 2
        for chunk in chunks:
            assert chunk.node_type == "document_section"
            assert chunk.language == "text"


class TestParseRstAsSingleChunk:
    def test_parse_rst_as_single_chunk(self):
        source = """\
My RST Document
===============

This is a reStructuredText document with some content.

Section
-------

More content here.
"""
        chunks = parse_doc_file("guide.rst", source, "restructuredtext")
        assert len(chunks) == 1
        assert chunks[0].language == "restructuredtext"
        assert chunks[0].node_type == "document_section"
        assert chunks[0].file_path == "guide.rst"


class TestParseConfigPackageJsonValid:
    def test_parse_config_package_json_valid(self):
        source = """\
{
  "name": "my-app",
  "version": "1.2.3",
  "description": "A sample application",
  "scripts": {
    "start": "node index.js",
    "test": "jest",
    "build": "webpack"
  },
  "dependencies": {
    "express": "^4.18.0",
    "lodash": "^4.17.21"
  }
}
"""
        chunks = parse_config_file("package.json", source)
        assert len(chunks) == 1
        chunk = chunks[0]
        assert chunk.name == "package.json"
        assert chunk.node_type == "config_file"
        assert chunk.language == "json"
        assert "Package:" in chunk.content
        assert "Scripts:" in chunk.content


class TestParseConfigPackageJsonInvalid:
    def test_parse_config_package_json_invalid(self):
        # Malformed JSON — should fall back to raw content chunk
        source = "{ this is not valid json !!!"
        chunks = parse_config_file("package.json", source)
        assert len(chunks) == 1
        chunk = chunks[0]
        assert chunk.name == "package.json"
        assert chunk.node_type == "config_file"
        # Content falls back to the raw source (truncated)
        assert "this is not valid json" in chunk.content


class TestParseConfigDockerCompose:
    def test_parse_config_docker_compose(self):
        source = """\
version: '3.8'
services:
  web:
    image: nginx:latest
    ports:
      - "80:80"
  db:
    image: postgres:15
    environment:
      POSTGRES_DB: mydb
"""
        chunks = parse_config_file("docker-compose.yml", source)
        assert len(chunks) == 1
        chunk = chunks[0]
        assert chunk.language == "yaml"
        assert chunk.node_type == "document_section"


class TestParseConfigPyproject:
    def test_parse_config_pyproject(self):
        source = """\
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "mypackage"
version = "0.1.0"
dependencies = ["requests", "pydantic"]
"""
        chunks = parse_config_file("pyproject.toml", source)
        assert len(chunks) == 1
        chunk = chunks[0]
        assert chunk.language == "toml"
        assert chunk.node_type == "document_section"


class TestParseDocFileMarkdown:
    def test_parse_doc_file_markdown(self):
        source = """\
## Section One

Enough content here to make the section longer than fifty characters total.

## Section Two

Also enough content in this section to exceed the fifty character minimum threshold.
"""
        chunks = parse_doc_file("readme.md", source, "markdown")
        # Confirms dispatch to _parse_markdown
        assert len(chunks) == 2
        node_types = {c.node_type for c in chunks}
        assert node_types == {"document_section"}


class TestParseDocFileEmpty:
    def test_parse_doc_file_empty(self):
        chunks = parse_doc_file("empty.md", "", "markdown")
        assert chunks == []

    def test_parse_doc_file_whitespace_only(self):
        chunks = parse_doc_file("blank.md", "   \n\t\n  ", "markdown")
        assert chunks == []


# ─────────────────────────────────────────────────────────────
# Tests for language_detector.py new functions
# ─────────────────────────────────────────────────────────────

class TestIsDocFile:
    def test_is_doc_file_md(self):
        assert is_doc_file("README.md") is True

    def test_is_doc_file_rst(self):
        assert is_doc_file("docs/guide.rst") is True

    def test_is_doc_file_txt(self):
        assert is_doc_file("notes.txt") is True

    def test_is_doc_file_py(self):
        assert is_doc_file("main.py") is False

    def test_is_doc_file_js(self):
        assert is_doc_file("index.js") is False

    def test_is_doc_file_uppercase_extension(self):
        # Extensions are lowercased before lookup
        assert is_doc_file("README.MD") is True


class TestIsConfigFile:
    def test_is_config_file_package_json(self):
        assert is_config_file("package.json") is True

    def test_is_config_file_docker_compose(self):
        assert is_config_file("docker-compose.yml") is True

    def test_is_config_file_env_example(self):
        assert is_config_file(".env.example") is True

    def test_is_config_file_random_json(self):
        # "data.json" is not in the whitelist
        assert is_config_file("data.json") is False

    def test_is_config_file_pyproject(self):
        assert is_config_file("pyproject.toml") is True

    def test_is_config_file_docker_compose_yaml(self):
        assert is_config_file("docker-compose.yaml") is True

    def test_is_config_file_with_path_prefix(self):
        # Should match on the filename only, regardless of directory path
        assert is_config_file("subdir/package.json") is True

    def test_is_config_file_unknown(self):
        assert is_config_file("settings.ini") is False


class TestDetectDocLanguage:
    def test_detect_doc_language_md(self):
        assert detect_doc_language("README.md") == "markdown"

    def test_detect_doc_language_rst(self):
        assert detect_doc_language("docs/guide.rst") == "restructuredtext"

    def test_detect_doc_language_txt(self):
        assert detect_doc_language("notes.txt") == "text"

    def test_detect_doc_language_unknown_defaults_to_text(self):
        # An unrecognised extension falls back to "text"
        assert detect_doc_language("file.log") == "text"

    def test_detect_doc_language_uppercase(self):
        # Extensions are lowercased before lookup
        assert detect_doc_language("README.MD") == "markdown"


class TestShouldSkipEnvFiles:
    def test_should_skip_env_file(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET=abc123")
        assert should_skip(str(env_file)) is True

    def test_should_skip_env_local(self, tmp_path):
        env_local = tmp_path / ".env.local"
        env_local.write_text("KEY=value")
        assert should_skip(str(env_local)) is True

    def test_should_skip_env_example(self, tmp_path):
        env_example = tmp_path / ".env.example"
        env_example.write_text("SECRET=changeme")
        # .env.example is in CONFIG_FILENAMES whitelist → should NOT be skipped
        assert should_skip(str(env_example)) is False


class TestShouldSkipMdFile:
    def test_should_skip_md_file(self, tmp_path):
        readme = tmp_path / "README.md"
        readme.write_text("# Hello\n\nSome documentation content.")
        # Doc files should NOT be skipped
        assert should_skip(str(readme)) is False


class TestShouldSkipLockFiles:
    def test_should_skip_composer_lock(self, tmp_path):
        lock_file = tmp_path / "composer.lock"
        lock_file.write_text("{}")
        assert should_skip(str(lock_file)) is True

    def test_should_skip_package_lock(self, tmp_path):
        lock_file = tmp_path / "package-lock.json"
        lock_file.write_text("{}")
        assert should_skip(str(lock_file)) is True

    def test_should_skip_yarn_lock(self, tmp_path):
        lock_file = tmp_path / "yarn.lock"
        lock_file.write_text("# yarn lockfile v1")
        assert should_skip(str(lock_file)) is True


class TestShouldSkipOversizedFile:
    def test_should_skip_oversized_doc_file(self, tmp_path):
        big_doc = tmp_path / "huge.md"
        # DOC_MAX_FILE_SIZE_BYTES = 100 * 1024 = 102400
        big_doc.write_bytes(b"A" * (102400 + 1))
        assert should_skip(str(big_doc)) is True

    def test_should_not_skip_normal_sized_doc_file(self, tmp_path):
        normal_doc = tmp_path / "normal.md"
        normal_doc.write_text("# Title\n\nSome content.")
        assert should_skip(str(normal_doc)) is False
