"""
结构化图表规格 → Mermaid 代码组装器。

LLM 输出 JSON spec（```diagram-spec 代码块），Python 程序化组装为合法 Mermaid 语法，
避免 LLM 直接生成 Mermaid 时的语法错误。
"""
import re
import logging
from typing import List, Optional, Literal, Union

from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)


# ── Flowchart ──────────────────────────────────────────────────────────────

_SHAPE_ALIASES: dict = {
    "database":    "subroutine",
    "cylinder":    "subroutine",
    "db":          "subroutine",   # Case 1: LLM 常用 "db" 表示数据库
    "circle":      "round",
    "ellipse":     "round",
    "hexagon":     "rect",
    "parallelogram": "rect",
    "trapezoid":   "rect",
    "default":     "rect",
}
_VALID_SHAPES = {"rect", "round", "diamond", "stadium", "subroutine"}


class FlowchartNode(BaseModel):
    id: str
    label: str
    shape: str = "rect"

    @field_validator("shape")
    @classmethod
    def normalize_shape(cls, v: str) -> str:
        if v in _VALID_SHAPES:
            return v
        return _SHAPE_ALIASES.get(v, "rect")

    @field_validator("id")
    @classmethod
    def id_must_be_ascii(cls, v: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_]", "_", v)
        if not cleaned or cleaned[0].isdigit():
            cleaned = "N_" + cleaned
        return cleaned

    @field_validator("label")
    @classmethod
    def label_max_length(cls, v: str) -> str:
        return v[:30] if len(v) > 30 else v


class FlowchartEdge(BaseModel):
    from_id: str
    to_id: str
    label: Optional[str] = None


class SubgraphSpec(BaseModel):
    id: str
    label: str
    node_ids: List[str]


class FlowchartSpec(BaseModel):
    type: Literal["flowchart"]
    direction: Literal["TD", "LR", "BT", "RL"] = "TD"
    nodes: List[FlowchartNode]
    edges: List[FlowchartEdge]
    subgraphs: Optional[List[SubgraphSpec]] = None


def _node_shape(node: FlowchartNode) -> str:
    label = node.label.replace('"', "'")
    shapes = {
        "rect":       f'["{label}"]',
        "round":      f'("{label}")',
        "diamond":    f'{{"{label}"}}',
        "stadium":    f'(["{label}"])',
        "subroutine": f'[["{label}"]]',
    }
    return shapes.get(node.shape, f'["{label}"]')


def assemble_flowchart(spec: FlowchartSpec) -> str:
    lines = [f"flowchart {spec.direction}"]
    node_map = {n.id: n for n in spec.nodes}
    subgraph_node_ids: set = set()

    if spec.subgraphs:
        for sg in spec.subgraphs:
            sg_id = re.sub(r"[^A-Za-z0-9_]", "_", sg.id)
            sg_label = sg.label[:30].replace('"', "'")
            lines.append(f'    subgraph {sg_id}["{sg_label}"]')
            for nid in sg.node_ids:
                if nid in node_map:
                    n = node_map[nid]
                    lines.append(f"        {n.id}{_node_shape(n)}")
                    subgraph_node_ids.add(nid)
            lines.append("    end")

    for node in spec.nodes:
        if node.id not in subgraph_node_ids:
            lines.append(f"    {node.id}{_node_shape(node)}")

    for edge in spec.edges:
        from_id = re.sub(r"[^A-Za-z0-9_]", "_", edge.from_id)
        to_id = re.sub(r"[^A-Za-z0-9_]", "_", edge.to_id)
        if edge.label:
            lbl = edge.label[:30].replace('"', "'")
            lines.append(f'    {from_id} -->|"{lbl}"| {to_id}')
        else:
            lines.append(f"    {from_id} --> {to_id}")

    return "\n".join(lines)


# ── ERDiagram ──────────────────────────────────────────────────────────────

_KEY_ALIASES: dict = {
    "Unique": "UK", "unique": "UK", "UNIQUE": "UK",
    "primary": "PK", "PRIMARY": "PK",
    "foreign": "FK", "FOREIGN": "FK",
}


class ERAttribute(BaseModel):
    type: str
    name: str
    key: Optional[Literal["PK", "FK", "UK"]] = None
    comment: Optional[str] = None

    @field_validator("key", mode="before")
    @classmethod
    def normalize_key(cls, v) -> Optional[str]:
        if not v:  # None 或空字符串
            return None
        mapped = _KEY_ALIASES.get(v, v)
        return mapped if mapped in {"PK", "FK", "UK"} else None


class EREntity(BaseModel):
    name: str
    attributes: List[ERAttribute] = []


class ERRelationship(BaseModel):
    from_entity: str
    to_entity: str
    cardinality: str  # e.g. "||--o{"
    label: str


class ERDiagramSpec(BaseModel):
    type: Literal["erDiagram"]
    entities: List[EREntity]
    relationships: List[ERRelationship]


def assemble_er_diagram(spec: ERDiagramSpec) -> str:
    lines = ["erDiagram"]
    for entity in spec.entities:
        lines.append(f"    {entity.name} {{")
        for attr in entity.attributes:
            key_str = f" {attr.key}" if attr.key else ""
            comment_str = f' "{attr.comment}"' if attr.comment else ""
            lines.append(f"        {attr.type} {attr.name}{key_str}{comment_str}")
        lines.append("    }")
    for rel in spec.relationships:
        label = rel.label
        # 中文标签必须加双引号
        if re.search(r"[\u4e00-\u9fff]", label) and not (
            label.startswith('"') and label.endswith('"')
        ):
            label = f'"{label}"'
        lines.append(
            f"    {rel.from_entity} {rel.cardinality} {rel.to_entity} : {label}"
        )
    return "\n".join(lines)


# ── SequenceDiagram ────────────────────────────────────────────────────────

class SequenceParticipant(BaseModel):
    alias: str
    name: str

    @field_validator("alias")
    @classmethod
    def alias_must_be_ascii(cls, v: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_]", "_", v)
        if not cleaned or cleaned[0].isdigit():
            cleaned = "P_" + cleaned
        return cleaned


_ARROW_ALIASES: dict = {
    "<--":  "-->",
    "<<--": "-->>",
    "<-":   "->",
    "<<-":  "->>",
}
_VALID_ARROWS = {"->", "->>", "-->", "-->>"}


class SequenceMessage(BaseModel):
    from_alias: str
    to_alias: str
    message: str
    arrow: str = "->>"
    activate: bool = False
    deactivate: bool = False

    @field_validator("arrow")
    @classmethod
    def normalize_arrow(cls, v: str) -> str:
        if v in _VALID_ARROWS:
            return v
        return _ARROW_ALIASES.get(v, "->>")  # 未知箭头默认 ->>


class SequenceNote(BaseModel):
    position: Literal["over", "left of", "right of"] = "over"
    participants: List[str]
    text: str


class SequenceDiagramSpec(BaseModel):
    type: Literal["sequenceDiagram"]
    participants: List[SequenceParticipant]
    messages: List[SequenceMessage]
    notes: Optional[List[SequenceNote]] = None

    @field_validator("messages", mode="before")
    @classmethod
    def filter_invalid_messages(cls, v):
        """过滤掉 LLM 混入的 loop/alt/else 伪对象（缺少 from_alias/to_alias）"""
        if not isinstance(v, list):
            return v
        valid = [
            m for m in v
            if isinstance(m, dict) and "from_alias" in m and "to_alias" in m
        ]
        skipped = len(v) - len(valid)
        if skipped:
            logger.warning(f"[DiagramAssembler] 过滤掉 {skipped} 个无效消息对象（缺少 from_alias/to_alias）")
        return valid


def assemble_sequence_diagram(spec: SequenceDiagramSpec) -> str:
    lines = ["sequenceDiagram"]
    for p in spec.participants:
        lines.append(f"    participant {p.alias} as {p.name}")

    # 追踪已激活的参与者，防止去激活未激活的参与者（Mermaid 渲染错误）
    active_set: set = set()

    for msg in spec.messages:
        from_a = re.sub(r"[^A-Za-z0-9_]", "_", msg.from_alias)
        to_a = re.sub(r"[^A-Za-z0-9_]", "_", msg.to_alias)
        if msg.activate:
            # A->>+B: 激活接收方 B
            active_set.add(to_a)
            lines.append(f"    {from_a}{msg.arrow}+{to_a}: {msg.message}")
        elif msg.deactivate:
            # A->>-B: 去激活发送方 A（Mermaid 语义：'-' 作用于发送方）
            # 若发送方未激活，跳过 '-' 避免渲染错误
            if from_a in active_set:
                active_set.discard(from_a)
                lines.append(f"    {from_a}{msg.arrow}-{to_a}: {msg.message}")
            else:
                logger.warning(
                    f"[DiagramAssembler] 跳过去激活：{from_a} 未被激活，忽略 deactivate 标志"
                )
                lines.append(f"    {from_a}{msg.arrow}{to_a}: {msg.message}")
        else:
            lines.append(f"    {from_a}{msg.arrow}{to_a}: {msg.message}")
    if spec.notes:
        for note in spec.notes:
            participants_str = ",".join(note.participants)
            lines.append(f"    Note {note.position} {participants_str}: {note.text}")
    return "\n".join(lines)


# ── Dispatcher ─────────────────────────────────────────────────────────────

_SPEC_MODELS = {
    "flowchart":       FlowchartSpec,
    "erDiagram":       ERDiagramSpec,
    "sequenceDiagram": SequenceDiagramSpec,
}

_ASSEMBLERS = {
    "flowchart":       assemble_flowchart,
    "erDiagram":       assemble_er_diagram,
    "sequenceDiagram": assemble_sequence_diagram,
}


def assemble_diagram(spec_json: dict) -> str:
    """
    将 JSON spec 组装为 Mermaid 代码。
    spec_json 必须包含 "type" 字段（flowchart / erDiagram / sequenceDiagram）。
    """
    diagram_type = spec_json.get("type")
    if diagram_type not in _SPEC_MODELS:
        raise ValueError(
            f"未知图表类型: {diagram_type!r}，支持: {list(_SPEC_MODELS.keys())}"
        )
    spec = _SPEC_MODELS[diagram_type].model_validate(spec_json)
    return _ASSEMBLERS[diagram_type](spec)
