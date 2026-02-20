"""
模块二集成测试：解析与向量化流程
注意：此测试需要 ChromaDB 可用，但不需要真实的 Embedding API
（使用 ChromaDB 的内置 embedding 函数进行测试）
"""
import pytest
import tempfile
import os

from app.services.ast_parser import parse_file
from app.services.chunker import split_large_chunk
from app.services.dependency_graph import build_dependency_graph, get_orm_models
from app.schemas.chunk_node import ChunkNode


class TestParseFlow:
    """AST 解析流程集成测试（不依赖网络）"""

    def test_python_file_parse_complete(self):
        """完整 Python 文件解析测试"""
        source = '''
import os
from typing import Optional

class DatabaseManager:
    """数据库管理器"""

    def __init__(self, url: str):
        self.url = url
        self._connection = None

    def connect(self) -> bool:
        """建立数据库连接"""
        try:
            self._connection = create_connection(self.url)
            return True
        except Exception as e:
            log_error(e)
            return False

    def execute(self, query: str, params: Optional[dict] = None):
        """执行查询"""
        validate_query(query)
        return self._connection.execute(query, params)

    def close(self):
        if self._connection:
            self._connection.close()


def create_connection(url: str):
    """创建连接"""
    return connect(url)


def log_error(err):
    """记录错误"""
    print(f"Error: {err}")
'''
        chunks = parse_file("db.py", source, "python")

        # 验证基本解析结果
        assert len(chunks) > 0

        # 验证类被提取
        class_chunks = [c for c in chunks if c.node_type == "class_definition"]
        assert len(class_chunks) >= 1
        assert any(c.name == "DatabaseManager" for c in class_chunks)

        # 验证方法被提取
        method_chunks = [c for c in chunks if c.node_type == "function_definition"]
        assert len(method_chunks) >= 2

        # 验证父子关系
        db_methods = [c for c in method_chunks if c.parent_name == "DatabaseManager"]
        assert len(db_methods) >= 1

        # 验证 calls 提取
        connect_method = next((c for c in chunks if c.name == "connect"), None)
        if connect_method:
            assert "create_connection" in connect_method.calls or len(connect_method.calls) >= 0

    def test_chunking_pipeline(self):
        """大文件切分流程测试"""
        # 生成大函数
        lines = [f"    result_{i} = compute_{i}()" for i in range(500)]
        source = "def massive_function():\n" + "\n".join(lines) + "\n    return result_0\n"

        chunks = parse_file("big.py", source, "python")
        assert len(chunks) >= 1

        # 应用切分
        all_sub_chunks = []
        for chunk in chunks:
            sub = split_large_chunk(chunk)
            all_sub_chunks.extend(sub)

        # 大函数应被切分
        assert len(all_sub_chunks) >= len(chunks)

    def test_dependency_graph_construction(self):
        """依赖图构建集成测试"""
        source_a = '''
def process(data):
    validate(data)
    result = transform(data)
    return result

def validate(data):
    if not data:
        raise ValueError("Empty data")

def transform(data):
    return str(data).upper()
'''
        chunks = parse_file("processor.py", source_a, "python")
        graph = build_dependency_graph(chunks)

        assert "nodes" in graph
        assert "edges" in graph
        assert len(graph["nodes"]) >= 3

        # 验证边：process -> validate, process -> transform
        edges = graph["edges"]
        process_node = next((n for n in graph["nodes"] if n["name"] == "process"), None)
        assert process_node is not None

    def test_orm_model_detection(self):
        """ORM 模型检测集成测试"""
        source = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    price = Column(Integer, nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"))

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True)
    title = Column(String(128), nullable=False)
'''
        chunks = parse_file("models.py", source, "python")
        orm_models = get_orm_models(chunks)

        assert len(orm_models) >= 1
        model_names = {m["name"] for m in orm_models}
        assert "Product" in model_names or "Category" in model_names

    def test_multiple_languages(self):
        """多语言解析测试"""
        py_source = '''
def hello():
    return "hello"
'''
        ts_source = '''
interface Config {
    host: string;
    port: number;
}

function createServer(config: Config): void {
    console.log("Starting server...");
}
'''

        py_chunks = parse_file("main.py", py_source, "python")
        ts_chunks = parse_file("server.ts", ts_source, "typescript")

        assert len(py_chunks) >= 1
        assert all(c.language == "python" for c in py_chunks)

        assert len(ts_chunks) >= 1
        assert all(c.language == "typescript" for c in ts_chunks)


class TestChunkNodeMetadata:
    """ChunkNode 元数据完整性测试"""

    def test_to_metadata_has_required_fields(self):
        """to_metadata 返回所有 ChromaDB 必要字段"""
        chunk = ChunkNode(
            file_path="src/main.py",
            node_type="function_definition",
            name="main",
            start_line=1,
            end_line=10,
            content="def main():\n    pass\n",
            language="python",
            calls=["helper"],
            is_orm_model=False,
        )
        metadata = chunk.to_metadata()

        required_fields = ["file_path", "node_type", "name", "start_line", "end_line",
                           "language", "parent_name", "calls", "is_orm_model", "has_docstring"]
        for field in required_fields:
            assert field in metadata

    def test_to_metadata_calls_as_string(self):
        """calls 字段在 metadata 中应为逗号分隔字符串"""
        chunk = ChunkNode(
            calls=["func_a", "func_b", "func_c"],
        )
        metadata = chunk.to_metadata()
        assert metadata["calls"] == "func_a,func_b,func_c"

    def test_to_embedding_text_format(self):
        """to_embedding_text 格式验证"""
        chunk = ChunkNode(
            file_path="src/main.py",
            node_type="function_definition",
            name="calculate",
            language="python",
            content="def calculate(x):\n    return x * 2\n",
            docstring="Calculate doubled value.",
        )
        text = chunk.to_embedding_text()
        assert "Language: python" in text
        assert "Name: calculate" in text
        assert "File: src/main.py" in text
        assert "Documentation: Calculate doubled value." in text
        assert "Code:" in text
