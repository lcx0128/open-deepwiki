import uuid
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class ChunkNode:
    """
    代码语义块节点。

    每个 ChunkNode 代表源代码中一个完整的逻辑单元
    （函数、类、导入语句等），附带完整的溯源元数据。
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    file_path: str = ""
    node_type: str = ""
    name: str = ""
    start_line: int = 0
    end_line: int = 0
    content: str = ""
    language: str = ""
    parent_id: Optional[str] = None
    parent_name: Optional[str] = None
    calls: List[str] = field(default_factory=list)
    decorators: List[str] = field(default_factory=list)
    docstring: Optional[str] = None
    is_orm_model: bool = False
    orm_fields: Optional[List[dict]] = None

    def to_metadata(self) -> dict:
        """转换为 ChromaDB 可存储的 metadata 字典（值必须是基础类型）"""
        return {
            "file_path": self.file_path,
            "node_type": self.node_type,
            "name": self.name,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "language": self.language,
            "parent_name": self.parent_name or "",
            "calls": ",".join(self.calls),
            "is_orm_model": self.is_orm_model,
            "has_docstring": bool(self.docstring),
        }

    def to_embedding_text(self) -> str:
        """生成用于 Embedding 的文本表示"""
        parts = [
            f"Language: {self.language}",
            f"Type: {self.node_type}",
            f"Name: {self.name}",
            f"File: {self.file_path}",
        ]
        if self.docstring:
            parts.append(f"Documentation: {self.docstring}")
        parts.append(f"Code:\n{self.content}")
        return "\n".join(parts)
