from typing import List
from app.schemas.chunk_node import ChunkNode

MAX_CHUNK_TOKENS = 6000       # embedding 模型最大 token 数（留余量）
OVERLAP_LINES = 20            # 滑动窗口重叠行数


def split_large_chunk(chunk: ChunkNode) -> List[ChunkNode]:
    """
    对超大 chunk 进行滑动窗口切分。

    算法：
    1. 估算 chunk token 数（简易方法：字符数 / 4）
    2. 如果未超限，原样返回
    3. 如果超限，按行切分为多个子 chunk
    4. 相邻子 chunk 之间保留 OVERLAP_LINES 行重叠

    重叠的目的：保证跨分片边界的代码逻辑不会被截断。
    """
    estimated_tokens = len(chunk.content) // 4
    if estimated_tokens <= MAX_CHUNK_TOKENS:
        return [chunk]

    lines = chunk.content.split("\n")
    total_lines = len(lines)
    window_size = MAX_CHUNK_TOKENS * 4 // 80  # 假设平均每行 80 字符
    step = window_size - OVERLAP_LINES

    if step <= 0:
        step = window_size

    sub_chunks = []
    start = 0
    part_index = 0

    while start < total_lines:
        end = min(start + window_size, total_lines)
        sub_content = "\n".join(lines[start:end])

        sub_chunk = ChunkNode(
            file_path=chunk.file_path,
            node_type=f"{chunk.node_type}_part",
            name=f"{chunk.name}__part_{part_index}",
            start_line=chunk.start_line + start,
            end_line=chunk.start_line + end - 1,
            content=sub_content,
            language=chunk.language,
            parent_id=chunk.id,
            parent_name=chunk.name,
            calls=chunk.calls if part_index == 0 else [],
            docstring=chunk.docstring if part_index == 0 else None,
        )
        sub_chunks.append(sub_chunk)

        start += step
        part_index += 1

    return sub_chunks
