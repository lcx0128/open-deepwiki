from typing import List, Optional
from tree_sitter import Node
from app.schemas.chunk_node import ChunkNode
from app.services.language_detector import get_parser
from app.services.orm_detector import _detect_orm_model

# ============================================================
# 各语言的关键节点类型映射
# ============================================================
EXTRACTABLE_NODE_TYPES = {
    "python": {
        "function_definition",      # def func():
        "class_definition",         # class Foo:
        # decorated_definition 已移除：避免与 function_definition/class_definition 重复提取
        # 装饰器通过 _extract_decorators 从 parent 节点获取
        "import_statement",         # import x
        "import_from_statement",    # from x import y
    },
    "javascript": {
        "function_declaration",     # function foo() {}
        "class_declaration",        # class Foo {}
        "arrow_function",           # const foo = () => {}
        "method_definition",        # methodName() {}
        "import_statement",         # import x from 'y'
        "export_statement",         # export default ...
    },
    "typescript": {
        "function_declaration",
        "class_declaration",
        "arrow_function",
        "method_definition",
        "interface_declaration",    # interface Foo {}
        "type_alias_declaration",   # type Foo = ...
        "import_statement",
        "export_statement",
    },
    "go": {
        "function_declaration",     # func foo() {}
        "method_declaration",       # func (r *Receiver) foo() {}
        "type_declaration",         # type Foo struct {}
        "import_declaration",       # import "fmt"
    },
    "rust": {
        "function_item",            # fn foo() {}
        "impl_item",                # impl Foo {}
        "struct_item",              # struct Foo {}
        "enum_item",                # enum Foo {}
        "trait_item",               # trait Foo {}
        "use_declaration",          # use std::io;
    },
    "java": {
        "method_declaration",       # public void foo() {}
        "class_declaration",        # public class Foo {}
        "interface_declaration",    # public interface Foo {}
        "import_declaration",       # import java.util.List;
    },
}


def parse_file(file_path: str, source_code: str, language: str) -> List[ChunkNode]:
    """
    解析单个源文件，提取所有有意义的代码块。

    算法流程：
    1. 使用 Tree-sitter 将源码解析为 AST
    2. 深度优先遍历 AST
    3. 遇到目标节点类型时提取为 ChunkNode
    4. 维护父子关系（类方法指向所属类）
    5. 提取函数调用列表

    返回: ChunkNode 列表
    """
    if not source_code.strip():
        return []

    parser = get_parser(language)
    tree = parser.parse(bytes(source_code, "utf-8"))
    root_node = tree.root_node

    extractable = EXTRACTABLE_NODE_TYPES.get(language, set())
    chunks: List[ChunkNode] = []

    def _extract_node(node: Node, parent_chunk: Optional[ChunkNode] = None):
        """递归遍历 AST，提取关键节点"""
        if node.type in extractable:
            chunk = _node_to_chunk(node, file_path, language, source_code, parent_chunk)
            chunks.append(chunk)

            # 对类的子节点继续提取（提取类方法）
            if node.type in ("class_definition", "class_declaration",
                             "impl_item", "class_body"):
                for child in node.children:
                    _extract_node(child, parent_chunk=chunk)
                return  # 已处理子节点，不再重复

        # 非目标节点，继续遍历子节点
        for child in node.children:
            _extract_node(child, parent_chunk)

    _extract_node(root_node)

    # 如果文件中没有提取到任何结构化节点，将整个文件作为一个 chunk
    if not chunks and len(source_code.strip()) > 0:
        chunks.append(ChunkNode(
            file_path=file_path,
            node_type="module",
            name=file_path.split("/")[-1],
            start_line=1,
            end_line=source_code.count("\n") + 1,
            content=source_code[:8000],  # 截断过长文件
            language=language,
        ))

    return chunks


def _node_to_chunk(
    node: Node,
    file_path: str,
    language: str,
    source_code: str,
    parent_chunk: Optional[ChunkNode],
) -> ChunkNode:
    """将单个 AST 节点转换为 ChunkNode"""
    # 提取节点名称
    name = _extract_name(node, language)

    # 提取代码内容
    content = source_code[node.start_byte:node.end_byte]

    # 提取文档字符串
    docstring = _extract_docstring(node, language, source_code)

    # 提取函数调用列表
    calls = _extract_calls(node, language)

    # 提取装饰器
    decorators = _extract_decorators(node, language, source_code)

    # 检测是否为 ORM 模型
    is_orm, orm_fields = _detect_orm_model(node, language, source_code)

    chunk = ChunkNode(
        file_path=file_path,
        node_type=node.type,
        name=name,
        start_line=node.start_point[0] + 1,  # Tree-sitter 使用 0-indexed
        end_line=node.end_point[0] + 1,
        content=content,
        language=language,
        parent_id=parent_chunk.id if parent_chunk else None,
        parent_name=parent_chunk.name if parent_chunk else None,
        calls=calls,
        decorators=decorators,
        docstring=docstring,
        is_orm_model=is_orm,
        orm_fields=orm_fields,
    )

    return chunk


def _extract_name(node: Node, language: str) -> str:
    """从 AST 节点提取函数/类名称"""
    for child in node.children:
        if child.type == "identifier":
            return child.text.decode("utf-8")
        if child.type == "name":
            return child.text.decode("utf-8")
        # TypeScript/Java 接口、类、类型别名：名称节点为 type_identifier
        if child.type == "type_identifier":
            return child.text.decode("utf-8")
        # Go 方法：方法名在参数列表之后
        if child.type == "field_identifier":
            return child.text.decode("utf-8")
    return "<anonymous>"


def _extract_calls(node: Node, language: str) -> List[str]:
    """
    提取函数体内的所有函数调用名称。
    这是构建依赖图的关键数据。
    """
    calls = set()

    def _walk(n: Node):
        if n.type == "call" or n.type == "call_expression":
            # 提取被调用的函数名
            for child in n.children:
                if child.type == "identifier":
                    calls.add(child.text.decode("utf-8"))
                elif child.type == "attribute" or child.type == "member_expression":
                    # obj.method() -> 只提取最后一个 identifier（方法名），避免将对象名也加入
                    last_id = None
                    for sub in child.children:
                        if sub.type in ("identifier", "property_identifier"):
                            last_id = sub.text.decode("utf-8")
                    if last_id and last_id not in ("self", "this", "cls"):
                        calls.add(last_id)
        for child in n.children:
            _walk(child)

    _walk(node)
    return sorted(calls)


def _extract_docstring(node: Node, language: str, source: str) -> Optional[str]:
    """提取函数/类的文档字符串"""
    if language == "python":
        # Python docstring: 函数体第一个 expression_statement 中的 string
        body = None
        for child in node.children:
            if child.type == "block":
                body = child
                break
        if body and body.children:
            first_stmt = body.children[0]
            if first_stmt.type == "expression_statement":
                for sc in first_stmt.children:
                    if sc.type == "string":
                        text = source[sc.start_byte:sc.end_byte]
                        # 移除引号
                        return text.strip().strip('"\' \n').strip('"""').strip("'''").strip()
    return None


def _extract_decorators(node: Node, language: str, source: str) -> List[str]:
    """提取装饰器列表"""
    decorators = []
    if language == "python":
        # decorated_definition 包含 decorator 子节点
        parent = node.parent
        if parent and parent.type == "decorated_definition":
            for child in parent.children:
                if child.type == "decorator":
                    decorators.append(source[child.start_byte:child.end_byte].strip())
    return decorators
