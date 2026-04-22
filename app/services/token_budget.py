# app/services/token_budget.py
from typing import List, Optional, Tuple

# 近似 Token 计算：英文 ~4 字符/token，中文 ~2 字符/token
def estimate_tokens(text: str) -> int:
    """粗略估算文本的 Token 数"""
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    non_ascii = len(text) - ascii_chars
    return (ascii_chars // 4) + (non_ascii // 2)


# 各模型的上下文窗口大小
MODEL_LIMITS = {
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-3.5-turbo": 16385,
    "qwen-plus": 131072,
    "qwen-turbo": 131072,
    "qwen-max": 32768,
    "gemini-1.5-pro": 1048576,
    "gemini-1.5-flash": 1048576,
    "gemini-2.0-flash": 1048576,
}

BUDGET_RATIO = 0.80  # 使用模型限制的 80% 作为预算
CONTEXT_BUDGET_RATIO = 0.6  # 剩余预算中分配给 RAG 上下文的比例


def apply_token_budget(
    messages: List[dict],
    model: str,
    system_prompt: str,
    rag_context: str,
    user_query: str,
) -> Tuple[List[dict], str]:
    """
    应用 Token 预算管理。

    返回: (trimmed_messages, trimmed_context)

    裁剪优先级（从低到高，先裁低优先级的）：
    1. 最旧的对话历史轮次
    2. RAG 代码上下文的尾部
    3. 绝不裁剪 system_prompt 和当前 user_query
    """
    limit = MODEL_LIMITS.get(model, 32000)
    budget = int(limit * BUDGET_RATIO)

    # 计算固定部分的 Token 数（不可裁剪）
    fixed_tokens = estimate_tokens(system_prompt) + estimate_tokens(user_query) + 100  # buffer

    remaining = budget - fixed_tokens
    if remaining <= 0:
        return [], ""

    # 先分配给 RAG 上下文（最多用 60% 剩余预算）
    context_budget = int(remaining * CONTEXT_BUDGET_RATIO)
    history_budget = remaining - context_budget

    # 裁剪 RAG 上下文（保持完整性，不做字符截断）
    # chunk-aware 裁剪已在调用方完成；此处仅做最终安全检查
    context_tokens = estimate_tokens(rag_context)
    if context_tokens > context_budget:
        chunks = rag_context.split("\n\n---\n\n")
        trimmed = trim_chunks_to_budget(chunks, context_budget)
        rag_context = "\n\n---\n\n".join(trimmed)

    # 裁剪对话历史（从最旧的开始删除）
    trimmed_messages = []
    used_tokens = 0
    for msg in reversed(messages):
        msg_tokens = estimate_tokens(msg.get("content", ""))
        if used_tokens + msg_tokens <= history_budget:
            trimmed_messages.insert(0, msg)
            used_tokens += msg_tokens
        else:
            break  # 超出预算，停止添加更旧的消息

    return trimmed_messages, rag_context


def trim_chunks_to_budget(
    chunks: List[str],
    budget_tokens: int,
    weights: Optional[List[float]] = None,
) -> List[str]:
    """
    以 chunk 为最小裁剪单位，按证据权重整块丢弃，直到总 token 数 <= budget_tokens。

    返回的 chunk 保持原始顺序；当预算极小或无法精确满足预算时，也至少保留 1 条。
    """
    if not chunks:
        return []

    chunk_info = []
    for i, chunk in enumerate(chunks):
        tokens = estimate_tokens(chunk)
        weight = weights[i] if weights and i < len(weights) else 0.5
        chunk_info.append((i, chunk, tokens, weight))

    total_tokens = sum(info[2] for info in chunk_info)
    if total_tokens <= budget_tokens:
        return list(chunks)

    drop_order = sorted(chunk_info, key=lambda x: (x[3], -x[0]))

    kept_indices = {info[0] for info in chunk_info}
    running_tokens = total_tokens
    for i, chunk, tokens, weight in drop_order:
        if running_tokens <= budget_tokens or len(kept_indices) == 1:
            break
        kept_indices.remove(i)
        running_tokens -= tokens

    return [chunks[i] for i in sorted(kept_indices)]
