"""
模块二单元测试：AST 解析器
测试 Tree-sitter 对各语言的解析能力和 ORM 模型检测
"""
import pytest
from app.services.ast_parser import parse_file


class TestPythonParser:
    """Python AST 解析测试"""

    def test_extract_function(self):
        source = '''
def hello(name: str) -> str:
    """Say hello."""
    return f"Hello, {name}"
'''
        chunks = parse_file("test.py", source, "python")
        assert len(chunks) >= 1
        func_chunks = [c for c in chunks if c.node_type == "function_definition"]
        assert len(func_chunks) == 1
        assert func_chunks[0].name == "hello"
        assert func_chunks[0].start_line == 2
        assert func_chunks[0].language == "python"
        assert func_chunks[0].file_path == "test.py"

    def test_extract_class_with_methods(self):
        source = '''
class UserService:
    """User service class."""

    def create_user(self, name):
        return User(name=name)

    def delete_user(self, user_id):
        self.repo.delete(user_id)
'''
        chunks = parse_file("service.py", source, "python")
        # 应提取：1个类 + 2个方法
        class_chunks = [c for c in chunks if c.node_type == "class_definition"]
        method_chunks = [c for c in chunks if c.node_type == "function_definition"]
        assert len(class_chunks) == 1
        assert class_chunks[0].name == "UserService"
        assert len(method_chunks) == 2
        # 方法的 parent 应指向类
        for mc in method_chunks:
            assert mc.parent_name == "UserService"

    def test_extract_calls(self):
        source = '''
def process_order(order):
    validate_order(order)
    price = calculate_price(order.items)
    send_notification(order.user)
    return price
'''
        chunks = parse_file("order.py", source, "python")
        func_chunks = [c for c in chunks if c.node_type == "function_definition"]
        assert len(func_chunks) >= 1
        calls = func_chunks[0].calls
        assert "validate_order" in calls
        assert "calculate_price" in calls
        assert "send_notification" in calls

    def test_detect_sqlalchemy_model(self):
        source = '''
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    email = Column(String(512))
    team_id = Column(Integer, ForeignKey("teams.id"))
'''
        chunks = parse_file("models.py", source, "python")
        assert len(chunks) >= 1
        class_chunks = [c for c in chunks if c.node_type == "class_definition"]
        assert len(class_chunks) >= 1
        user_chunk = class_chunks[0]
        assert user_chunk.is_orm_model is True
        assert user_chunk.orm_fields is not None
        assert len(user_chunk.orm_fields) >= 3
        # 检查主键
        pk_fields = [f for f in user_chunk.orm_fields if f.get("primary_key")]
        assert len(pk_fields) >= 1
        # 检查外键
        fk_fields = [f for f in user_chunk.orm_fields if f.get("foreign_key")]
        assert len(fk_fields) >= 1

    def test_empty_file(self):
        chunks = parse_file("empty.py", "", "python")
        assert chunks == []

    def test_file_with_only_comments(self):
        source = "# This is a comment\n# Another comment\n"
        chunks = parse_file("comments.py", source, "python")
        # 无结构化节点时，应生成一个 module 级 chunk
        assert len(chunks) == 1
        assert chunks[0].node_type == "module"

    def test_chunk_has_content(self):
        source = '''
def add(a: int, b: int) -> int:
    return a + b
'''
        chunks = parse_file("math.py", source, "python")
        assert len(chunks) >= 1
        assert len(chunks[0].content) > 0
        assert "def add" in chunks[0].content

    def test_multiple_functions(self):
        source = '''
def foo():
    pass

def bar():
    return 42

def baz(x, y):
    return x + y
'''
        chunks = parse_file("funcs.py", source, "python")
        func_chunks = [c for c in chunks if c.node_type == "function_definition"]
        assert len(func_chunks) == 3
        names = {c.name for c in func_chunks}
        assert names == {"foo", "bar", "baz"}

    def test_import_extraction(self):
        source = '''
import os
import sys
from pathlib import Path
from typing import Optional, List

def main():
    pass
'''
        chunks = parse_file("imports.py", source, "python")
        # 应包含 import 语句和函数
        node_types = {c.node_type for c in chunks}
        assert "function_definition" in node_types or "import_statement" in node_types


class TestTypeScriptParser:
    """TypeScript AST 解析测试"""

    def test_extract_interface(self):
        source = '''
interface UserProps {
    name: string;
    age: number;
    email?: string;
}
'''
        chunks = parse_file("types.ts", source, "typescript")
        assert any(c.node_type == "interface_declaration" for c in chunks)

    def test_extract_function_declaration(self):
        source = '''
function greet(name: string): string {
    return `Hello, ${name}!`;
}
'''
        chunks = parse_file("greet.ts", source, "typescript")
        func_chunks = [c for c in chunks if c.node_type == "function_declaration"]
        assert len(func_chunks) >= 1
        assert func_chunks[0].name == "greet"

    def test_extract_class(self):
        source = '''
class Calculator {
    private value: number = 0;

    add(n: number): Calculator {
        this.value += n;
        return this;
    }

    result(): number {
        return this.value;
    }
}
'''
        chunks = parse_file("calc.ts", source, "typescript")
        class_chunks = [c for c in chunks if c.node_type == "class_declaration"]
        assert len(class_chunks) >= 1
        assert class_chunks[0].name == "Calculator"


class TestLineNumbers:
    """行号准确性测试"""

    def test_start_line_is_1indexed(self):
        source = 'def foo():\n    pass\n'
        chunks = parse_file("test.py", source, "python")
        func_chunks = [c for c in chunks if c.node_type == "function_definition"]
        assert func_chunks[0].start_line == 1  # 1-indexed

    def test_end_line_greater_than_start(self):
        source = '''
def multi_line():
    a = 1
    b = 2
    return a + b
'''
        chunks = parse_file("test.py", source, "python")
        func_chunks = [c for c in chunks if c.node_type == "function_definition"]
        assert len(func_chunks) >= 1
        assert func_chunks[0].end_line > func_chunks[0].start_line
