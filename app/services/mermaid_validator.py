import re
import logging
from typing import List
from app.services.llm.adapter import BaseLLMAdapter
from app.schemas.llm import LLMMessage

logger = logging.getLogger(__name__)

# Mermaid 代码块正则
MERMAID_BLOCK_RE = re.compile(r'```mermaid\s*\n(.*?)\n\s*```', re.DOTALL)

# 常见语法错误检测规则
MERMAID_RULES = [
    (re.compile(r'graph\s+LR'), "禁止使用 graph LR，必须使用 graph TD"),
    (re.compile(r'[（）【】]'), "禁止使用中文括号"),
    (re.compile(r'\([^)]{50,}\)'), "节点标签过长（超过50字符）"),
    # 排除合法的激活(+)和去激活(-)修饰符，以及节点标识符（单词字符）
    (re.compile(r'-->>(?![+\-\w])[^:\s]'), "箭头后缺少空格或冒号"),
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
            logger.warning(f"[MermaidValidator] 发现 {len(errors)} 个错误，第 {attempt + 1} 次修复")
            fix_prompt = (
                f"The following Mermaid diagram has syntax errors:\n\n"
                f"```mermaid\n{block}\n```\n\n"
                f"Errors found:\n"
                + "\n".join(f"- {e}" for e in errors)
                + "\n\nPlease fix ALL errors and return ONLY the corrected Mermaid code "
                  "(without ```mermaid wrapper).\n"
                  "Remember: use graph TD (not LR), proper arrow syntax, and short node labels."
            )

            try:
                fix_response = await adapter.generate_with_rate_limit(
                    messages=[LLMMessage(role="user", content=fix_prompt)],
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
