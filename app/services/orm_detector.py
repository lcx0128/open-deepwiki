import re
from typing import Tuple, Optional, List
from tree_sitter import Node

# ORM 基类名称匹配
ORM_BASE_CLASSES = {
    # SQLAlchemy
    "Base", "DeclarativeBase", "Model", "db.Model",
    # Django
    "models.Model",
}


def _detect_orm_model(
    node: Node, language: str, source: str
) -> Tuple[bool, Optional[List[dict]]]:
    """
    检测 AST 节点是否为 ORM 模型类定义。

    检测策略：
    1. 节点必须是 class_definition 或 class_declaration
    2. 父类列表中包含已知的 ORM 基类名
    3. 类体内包含 Column() 或 mapped_column() 调用

    返回: (is_orm: bool, fields: Optional[List[dict]])
    """
    if node.type not in ("class_definition", "class_declaration"):
        return False, None

    # 检查继承列表
    is_orm = False
    for child in node.children:
        if child.type == "argument_list":  # Python class Foo(Base)
            bases_text = source[child.start_byte:child.end_byte]
            for base in ORM_BASE_CLASSES:
                if re.search(rf'\b{re.escape(base)}\b', bases_text):
                    is_orm = True
                    break

    if not is_orm:
        return False, None

    # 提取字段定义
    fields = _extract_orm_fields(node, language, source)
    return True, fields


def _extract_orm_fields(node: Node, language: str, source: str) -> List[dict]:
    """
    从 ORM 模型类中提取字段定义。

    针对 SQLAlchemy 的典型模式：
        id = Column(Integer, primary_key=True)
        name = Column(String(255), nullable=False)
        user_id = Column(Integer, ForeignKey("users.id"))
    """
    fields = []

    def _walk(n: Node):
        # 寻找赋值语句: name = Column(...)
        if n.type in ("assignment", "expression_statement"):
            text = source[n.start_byte:n.end_byte]
            if "Column(" in text or "mapped_column(" in text:
                field_info = _parse_column_definition(text)
                if field_info:
                    fields.append(field_info)
        for child in n.children:
            _walk(child)

    _walk(node)
    return fields


def _parse_column_definition(text: str) -> Optional[dict]:
    """
    解析单行 Column 定义。

    输入: "id = Column(Integer, primary_key=True)"
    输出: {"name": "id", "type": "Integer", "primary_key": true, "nullable": true, "foreign_key": null}
    """
    # 提取字段名
    match = re.match(r'(\w+)\s*=\s*(?:Column|mapped_column)\((.+)\)', text.strip(), re.DOTALL)
    if not match:
        return None

    name = match.group(1)
    args = match.group(2)

    # 提取类型
    col_type = "Unknown"
    type_match = re.match(r'(\w+)', args)
    if type_match:
        col_type = type_match.group(1)

    # 提取约束
    is_pk = "primary_key=True" in args or "primary_key=true" in args
    is_nullable = "nullable=False" not in args  # 默认可空
    fk_match = re.search(r'ForeignKey\(["\']([^"\']+)["\']\)', args)
    foreign_key = fk_match.group(1) if fk_match else None

    return {
        "name": name,
        "type": col_type,
        "primary_key": is_pk,
        "nullable": is_nullable,
        "foreign_key": foreign_key,
    }
