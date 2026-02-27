import re
import logging
from typing import List, Tuple
from app.services.llm.adapter import BaseLLMAdapter
from app.schemas.llm import LLMMessage

logger = logging.getLogger(__name__)

# Mermaid 代码块正则
MERMAID_BLOCK_RE = re.compile(r'```mermaid\s*\n(.*?)\n\s*```', re.DOTALL)

# Few-shot 修复示例，按错误类型索引
_FIX_EXAMPLES: dict[str, str] = {
    "中文节点": """\
## Example: Chinese text used as node IDs

WRONG (causes "Syntax error" in Mermaid 10.x):
graph TD
    客户端 --> API网关 --> 服务层
    API网关 --> 数据库层

CORRECT (ASCII node IDs, Chinese only inside [ ] labels):
graph TD
    A[客户端] --> B[API网关] --> C[服务层]
    B --> D[数据库层]

WRONG:
graph TD
    用户请求[用户请求] --> 路由层[路由层]

CORRECT:
graph TD
    UserReq[用户请求] --> Router[路由层]

Rule: Node IDs must be ASCII letters/numbers/underscores. Chinese text must only appear inside square brackets [ ].
""",

    "graph LR": """\
## Example: graph LR is forbidden

WRONG:
graph LR
    A[开始] --> B[处理] --> C[结束]

CORRECT:
graph TD
    A[开始] --> B[处理] --> C[结束]

Rule: Always use `graph TD` (top-down). Never use `graph LR`.
""",

    "括号": """\
## Example: Unmatched brackets

WRONG:
graph TD
    A[客户端 --> B[API网关]
    B --> C[服务层]

CORRECT:
graph TD
    A[客户端] --> B[API网关]
    B --> C[服务层]

WRONG:
graph TD
    A(处理节点 --> B[结果]

CORRECT:
graph TD
    A(处理节点) --> B[结果]

Rule: Every opening bracket [ or ( must have a matching closing bracket ] or ).
""",

    "标签过长": """\
## Example: Node labels too long (max 30 characters)

WRONG:
graph TD
    A[这是一个描述非常详细超过三十个字符的服务组件名称] --> B[目标]

CORRECT:
graph TD
    A[详细服务组件] --> B[目标]

Rule: Keep node labels under 30 characters. Abbreviate long names.
""",

    "erDiagram中文": """\
## Example: erDiagram Chinese relationship labels must use double quotes

WRONG (causes 'Expecting ALPHANUM got 中'):
erDiagram
    Project ||--o{ Wiki : 拥有
    User ||--o{ Task : 创建
    Repository ||--|{ FileState : 包含

CORRECT (wrap Chinese labels in double quotes):
erDiagram
    Project ||--o{ Wiki : "拥有"
    User ||--o{ Task : "创建"
    Repository ||--|{ FileState : "包含"

Rule: In erDiagram, any relationship label after the colon (:) that contains Chinese or non-ASCII characters MUST be wrapped in double quotes "". ASCII-only labels do not need quotes.
""",
}


def _build_fix_messages(block: str, errors: List[str]) -> Tuple[str, str]:
    """
    根据检测到的错误类型，构建包含针对性 few-shot 示例的修复提示。
    返回 (system_content, user_content)。
    """
    system_content = (
        "You are a Mermaid diagram syntax expert. "
        "Your only job is to return corrected Mermaid code. "
        "Follow the examples exactly. "
        "Output ONLY the raw Mermaid code — no explanation, no ```mermaid wrapper."
    )

    # 按检测到的错误类型选择相关示例
    error_text = " ".join(errors)
    selected: List[str] = []
    if "中文" in error_text or "节点ID" in error_text:
        selected.append(_FIX_EXAMPLES["中文节点"])
    if "graph LR" in error_text:
        selected.append(_FIX_EXAMPLES["graph LR"])
    if "括号" in error_text:
        selected.append(_FIX_EXAMPLES["括号"])
    if "过长" in error_text:
        selected.append(_FIX_EXAMPLES["标签过长"])
    if "erDiagram" in error_text or ("中文" in error_text and "双引号" in error_text):
        if "erDiagram中文" in _FIX_EXAMPLES:
            selected.append(_FIX_EXAMPLES["erDiagram中文"])

    examples_section = ("\n".join(selected) + "\n---\n\n") if selected else ""

    error_list = "\n".join(f"  - {e}" for e in errors)
    user_content = (
        f"{examples_section}"
        f"Fix the following Mermaid diagram.\n\n"
        f"Errors detected:\n{error_list}\n\n"
        f"Broken diagram:\n"
        f"```mermaid\n{block}\n```\n\n"
        "Return ONLY the corrected Mermaid code (no wrapper, no explanation)."
    )

    return system_content, user_content

# 常见语法错误检测规则
MERMAID_RULES = [
    (re.compile(r'graph\s+LR'), "禁止使用 graph LR，必须使用 graph TD"),
    (re.compile(r'[（）【】]'), "禁止使用中文括号"),
    (re.compile(r'\([^)]{50,}\)'), "节点标签过长（超过50字符）"),
    # 排除合法的激活(+)和去激活(-)修饰符，以及节点标识符（单词字符）
    (re.compile(r'-->>(?![+\-\w])[^:\s]'), "箭头后缺少空格或冒号"),
    # erDiagram 中文关系标签未加双引号（冒号后直接跟中文，未用引号包裹）
    (re.compile(r':\s+(?!")[\u4e00-\u9fff\u3400-\u4dbf]'), "erDiagram/关系图中中文标签必须用双引号包裹，例：: 拥有 → : \"拥有\""),
]

# 不包含 {} ：erDiagram 使用 ||--o{ 这类不对称的花括号表示基数，无需匹配
BRACKET_PAIRS = {'(': ')', '[': ']'}


def validate_mermaid(mermaid_code: str) -> List[str]:
    """
    校验 Mermaid 代码，返回错误列表。空列表表示通过校验。
    """
    errors = []
    for pattern, description in MERMAID_RULES:
        if pattern.search(mermaid_code):
            errors.append(description)

    # 检查 graph/flowchart 图表中是否存在中文节点ID
    # Mermaid 10.x 要求节点ID只能含 ASCII 字符，中文必须放在标签内如 A[中文标签]
    if re.search(r'^\s*(?:graph|flowchart)\s+', mermaid_code, re.MULTILINE | re.IGNORECASE):
        # 去除标签括号内容后检查是否还有中文字符
        code_no_labels = re.sub(
            r'\[[^\]\n]*\]|\([^)\n]*\)|\{[^}\n]*\}|"[^"\n]*"', '', mermaid_code
        )
        if re.search(r'[\u4e00-\u9fff]', code_no_labels):
            errors.append(
                "节点ID含中文字符：Mermaid 10.x 节点ID必须用ASCII英文，"
                "中文只能放在标签内，示例：A[API网关] --> B[服务层]"
            )

    # 括号匹配检查（跳过字符串字面量和注释）
    stack = []
    in_quote = False
    for i, char in enumerate(mermaid_code):
        if char == '"' or char == "'":
            in_quote = not in_quote
            continue
        if in_quote:
            continue
        if char in BRACKET_PAIRS:
            stack.append(char)
        elif char in BRACKET_PAIRS.values():
            if not stack:
                errors.append(f"多余的闭合括号: {char}")
            else:
                expected = BRACKET_PAIRS[stack.pop()]
                if char != expected:
                    errors.append(f"括号不匹配: 期望 {expected}，实际 {char}")
    if stack:
        errors.append(f"未闭合的括号: {''.join(stack)}")

    return errors


async def validate_and_fix_mermaid(
    adapter: BaseLLMAdapter, model: str, content: str, max_retries: int = 3
) -> str:
    """
    校验并修复 Markdown 内容中的所有 Mermaid 代码块。

    自愈循环：
    1. 提取所有 ```mermaid 代码块
    2. 对每个代码块进行校验
    3. 如果有错误，将错误信息 + 原代码发回 LLM 修复
    4. 最多重试 max_retries 次
    5. 如果仍无法修复，退化为 ```text 代码块
    """
    for attempt in range(max_retries):
        blocks = MERMAID_BLOCK_RE.findall(content)
        if not blocks:
            break

        all_valid = True
        for block in blocks:
            errors = validate_mermaid(block)
            if not errors:
                continue

            all_valid = False
            logger.warning(
                f"[MermaidValidator] 发现 {len(errors)} 个错误，第 {attempt + 1} 次修复: "
                + "; ".join(errors)
            )
            system_content, user_content = _build_fix_messages(block, errors)

            try:
                fix_response = await adapter.generate_with_rate_limit(
                    messages=[
                        LLMMessage(role="system", content=system_content),
                        LLMMessage(role="user", content=user_content),
                    ],
                    model=model,
                    temperature=0.1,
                )
                fixed_block = fix_response.content.strip()
                # 清理可能残留的 wrapper
                fixed_block = re.sub(r'^```mermaid\s*\n?', '', fixed_block)
                fixed_block = re.sub(r'\n?```\s*$', '', fixed_block)

                content = content.replace(
                    f"```mermaid\n{block}\n```",
                    f"```mermaid\n{fixed_block}\n```",
                    1,
                )
            except Exception as e:
                logger.error(f"[MermaidValidator] Mermaid 修复调用失败: {e}")
            break  # 重新从头校验

        if all_valid:
            break
    else:
        # 超过最大重试次数，将有问题的 Mermaid 块退化为 text 代码块
        def _degrade(match):
            code = match.group(1)
            errors = validate_mermaid(code)
            if errors:
                logger.warning(f"[MermaidValidator] 无法修复，退化为 text 块")
                return f"```text\n{code}\n```"
            return match.group(0)

        content = MERMAID_BLOCK_RE.sub(_degrade, content)

    return content
