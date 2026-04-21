# RAG / AI Chat 升级策略文档

> 生成时间：2026-04-21
> 用途：作为后续各功能 Plan 文档的生成上下文，描述架构层修改方案
> 优先级排序依据：`RAG_CHAT_ARCH_REVIEW_2026-04-21.md` + `CHAT_CONTEXT_REVIEW_2026-04-21.md`

---

## 功能一：Token Budget Chunk-aware 裁剪（第一优先级）

### 1.1 改造前后的问答行为差异

**改造前**

RAG 上下文超出预算时，系统按字符比例整体截断：

```
rag_context = rag_context[:int(len(rag_context) * ratio)]
```

结果是一个连续字符串从某个任意位置被切断。对代码问答来说，被切断的位置可能正好在：

- 某个函数体中间
- 某段 JSON / prompt 中间
- 某个多文件调用链的关键一跳处

用户体验：问答结果看起来基于了若干文件，但关键函数被截半，模型推理时证据残缺，容易给出错误结论或"我没有足够信息"。

**改造后**

RAG 上下文以 **chunk 为最小裁剪单位**，按证据权重排序后从末尾整块丢弃。用户感受：

- 所有进入上下文的代码片段都是完整的
- 被裁掉的是"相对次要"的 chunk，而不是最重要 chunk 的后半段
- Token 超限时答案质量下降是"信息不够"，而不是"信息残缺"

### 1.2 改造后的完整流程

```
fuse_query()
    ↓
stage1_discovery()  →  返回带权重的 CodeGuideline 列表
    ↓
stage2_assembly()   →  拉取完整 chunk 正文，每条 chunk 为独立字符串单元
    ↓
[新增] chunk_budget_trim()
    - 输入：List[chunk_text]（已有完整内容）+ token 预算
    - 按 chunk 权重排序（保留最高权重）
    - 从末尾整块删除，直到总 token ≤ 预算
    - 输出：List[chunk_text]（全部为完整内容）
    ↓
apply_token_budget()  →  仅处理历史裁剪，RAG context 已由上一步保证完整
    ↓
LLM 生成
```

### 1.3 需要修改的模块

| 模块 | 修改性质 |
|---|---|
| `app/services/token_budget.py` | 核心重构：新增 chunk-aware 裁剪逻辑 |
| `app/services/chat_service.py` | 调用层适配：在 stage2_assembly 后、apply_token_budget 前插入新裁剪步骤 |

### 1.4 架构层修改方案

**`app/services/token_budget.py`**

- 新增函数 `trim_chunks_to_budget(chunks: List[str], budget_tokens: int, weights: Optional[List[float]] = None) -> List[str]`
  - `chunks`：每条为完整 chunk 正文（来自 `stage2_assembly` 输出）
  - `weights`：可选，对应 `CodeGuideline.score`，无权重时视为均等
  - 策略：**内部**按权重降序确定保留集合（决定"哪些 chunk 进入上下文"），但**输出时必须按原始顺序重排**（即按 chunk 在原始 `code_contents` 列表中的索引还原顺序）
  - 原因：LLM 读取代码上下文时，相邻代码片段的前后顺序影响语义连贯性；若以权重顺序输出，高权重 chunk 可能出现在低权重 chunk 之后导致调用链顺序错乱
  - 始终保留至少 1 条 chunk（防止 context 完全清空）
- 保留原 `apply_token_budget()` 不变，其职责收缩为：仅处理历史消息裁剪 + 最终 rag_context 字符串的拼接（不再做字符截断）
- 内部移除第 63-65 行的字符比例截断逻辑，改为调用 `trim_chunks_to_budget()`

**`app/services/chat_service.py`**

- `handle_chat()`、`handle_chat_stream()`、`handle_deep_research_stream()` 三处
- 在 `stage2_assembly()` 返回 `code_contents: List[str]` 后、`"\n\n---\n\n".join(code_contents)` 前，插入调用 `trim_chunks_to_budget(code_contents, context_budget, weights)`
- `weights` 来自对应的 `CodeGuideline.score` 列表，需在 `stage2_assembly` 调用处同步传递下来

---

## 功能二：新增 Grep 层 + 路径搜索层（第一优先级）

### 2.1 改造前后的问答行为差异

**改造前**

当用户询问：
- "JWT 相关的配置项在哪里"
- "INCREMENTAL_SYNC 这个任务类型定义在哪"
- "路由 `/api/repositories` 在哪个文件"
- "这个报错 `MultipleResultsFound` 在哪里被处理"

系统走向量检索。向量检索擅长语义相似，但对精确标识符、路由字符串、配置键名等文本精确匹配天然不准。结果是这类问题常常找不到最相关的片段，模型被迫依赖不够精准的背景上下文作答。

**改造后**

在 stage1_discovery 之外，新增两条并行的轻量检索通道：

1. **路径搜索**：根据 query 中的关键词在 `RepoIndex` 的文件路径中做 token 级匹配，返回疑似相关的文件列表
2. **Grep 搜索**：对克隆到本地的代码仓库做文本级精确搜索，支持标识符、字面量、路由字符串等

这两条通道的结果与 stage1 向量结果合并，进入 stage2 装配。

用户感受：精确标识符类问题（"X 在哪里定义"、"某个常量叫什么"）显著更准，首轮命中率提高，减少需要二轮补检索的比例。

### 2.2 改造后的完整流程

```
fuse_query()
    ↓
并行执行：
  ├── stage1_discovery()         →  向量 + name + node_type 召回
  ├── search_file_paths()        →  路径 token 匹配（基于 RepoIndex 文件列表）
  └── grep_codebase()            →  本地文本精确搜索（基于克隆仓库目录）
    ↓
[新增] merge_retrieval_results()
    - 对三路结果去重（同文件同行号）
    - 为 grep/路径命中结果赋予基础分值
    - 合并为统一 CodeGuideline 列表（保留来源标记 source: "vector"|"path"|"grep"）
    ↓
stage2_assembly()
    ↓
trim_chunks_to_budget()  （来自功能一）
    ↓
LLM 生成
```

### 2.3 需要修改的模块

| 模块 | 修改性质 |
|---|---|
| `app/services/code_searcher.py` | 新增文件：实现 `search_file_paths()` 和 `grep_codebase()` |
| `app/services/two_stage_retriever.py` | 新增 `merge_retrieval_results()`，集成三路召回 |
| `app/services/chat_service.py` | 调用层：替换单路 stage1 调用为三路并行 + 合并 |
| `app/schemas/chunk_node.py`（或 llm.py） | `CodeGuideline` 新增 `source` 字段标记来源 |

### 2.4 架构层修改方案

**新增 `app/services/code_searcher.py`**

包含两个独立函数：

- `search_file_paths(repo_id: str, query: str) -> List[str]`
  - 从 `RepoIndex.index_data`（JSON）中提取全部文件路径
  - 将 query 按 CamelCase / snake_case / 路径分隔符拆词
  - 做关键词 token 匹配，返回命中文件的相对路径列表
  - 不调用 LLM，纯内存操作

- `grep_codebase(repo_id: str, patterns: List[str], context_lines: int = 3) -> List[GrepMatch]`
  - 基于克隆到 `settings.REPOS_BASE_DIR/{repo_id}/` 的本地仓库
  - **实现策略**：优先尝试调用系统 `rg`（ripgrep），若不可用则自动降级为 Python 原生文本搜索（逐行 `str.find` / `re.search`）
  - **Windows 兼容性约束**：本项目明确支持 Windows 部署（Celery 使用 `--pool=solo`、rmtree 有只读文件修复），Windows 环境不能预设 `rg` 已安装。因此 Python fallback 是**正式兼容路径**，不是可选优化——实现时必须保证在无 `rg` 的 Windows 环境下行为正确，且结果格式与 `rg` 路径一致
  - 过滤二进制文件、`node_modules/`、`.git/` 等目录（两条路径均需过滤）
  - 返回 `GrepMatch`（file_path, line_no, line_content, context_before, context_after）
  - `patterns` 从 query 中提取：精确标识符、带引号字面量、路由路径等

新增 schema `GrepMatch`（可放在 `app/schemas/chunk_node.py`）：
```
GrepMatch:
  file_path: str
  line_no: int
  line_content: str
  context_lines: List[str]
```

**`app/services/two_stage_retriever.py`**

- 新增 `merge_retrieval_results(vector_guidelines, path_files, grep_matches, repo_id) -> List[CodeGuideline]`
  - 将路径命中文件转为 `CodeGuideline`（score 设为固定基础值，如 0.6）
  - 将 grep 命中行附近范围转为 `CodeGuideline`（score 设为较高基础值，如 0.75，因为精确命中）
  - 与向量结果去重（相同 file_path + 行范围重叠视为重复，保留分值更高的）
  - 返回合并去重后的统一列表

**`app/services/chat_service.py`**

- 三个入口函数（`handle_chat`、`handle_chat_stream`、`handle_deep_research_stream`）
- 将单次 `stage1_discovery()` 调用改为：
  ```
  并发调用 stage1_discovery + search_file_paths + grep_codebase
  再调用 merge_retrieval_results 合并
  ```
- query 中提取 grep 关键词逻辑可以是简单规则（引号内容、驼峰标识符、`/api/` 前缀路径），不需要 LLM

---

## 功能三：证据充分性判断 + 自动二轮检索（第一优先级）

### 3.1 改造前后的问答行为差异

**改造前**

普通 chat 是单轮决策：检索一次 → 拼 prompt → 回答。无论首轮命中质量如何，都直接生成答案。对"为什么这里会失败"、"完整调用链是什么"这类需要跨文件证据的问题，首轮检索经常遗漏关键部分，模型却仍然给出不完整的答案，不会主动告诉用户"我还没看到关键代码"。

**改造后**

普通 chat 变为两阶段决策：

1. **首轮检索**：正常流程，得到 chunk 列表
2. **[新增] 证据充分性判断**：轻量规则 + 可选 LLM 判断"当前证据是否足以回答该问题"
3. **若判断不足**：自动触发二轮补检索（grep、路径搜索、依赖图一跳），拉取补充证据
4. **再生成**：基于合并后的证据回答

对用户完全透明，不需要手动切换 Deep Research。Deep Research 仍然保留，作为"主动要求多轮深度研究"的模式。

### 3.2 改造后的完整流程

```
fuse_query()
    ↓
三路并行检索（来自功能二）
    ↓
merge_retrieval_results() → 得到首轮 code_contents
    ↓
[新增] check_evidence_sufficiency(query, code_contents) -> EvidenceCheckResult
    - 规则检查（快速）：
        * code_contents 是否为空或极少
        * query 包含"调用链"/"完整流程"/"为什么"等复杂问题特征词
        * 首轮命中文件数 < 2 但 query 暗示跨文件
    - 返回 EvidenceCheckResult:
        is_sufficient: bool
        missing_aspects: List[str]  # 如 ["调用链", "配置项", "入口函数"]
        suggested_queries: List[str]  # 补充检索建议词
    ↓
若 is_sufficient = False：
    [新增] run_supplemental_retrieval(missing_aspects, suggested_queries, repo_id)
        - 对每个 suggested_query 调用 grep_codebase()
        - 若 missing_aspects 含"调用链"，触发依赖图一跳扩展（功能四）
        - 合并补充结果到 code_contents（去重）
    ↓
trim_chunks_to_budget()
    ↓
LLM 生成
```

最多执行一次补充检索（不循环），控制时延。

### 3.3 需要修改的模块

| 模块 | 修改性质 |
|---|---|
| `app/services/evidence_checker.py` | 新增文件：实现 `check_evidence_sufficiency()` |
| `app/services/chat_service.py` | 在三路检索后插入充分性判断 + 条件二轮检索 |

### 3.4 架构层修改方案

**新增 `app/services/evidence_checker.py`**

包含：
- `EvidenceCheckResult` dataclass：`is_sufficient: bool`、`missing_aspects: List[str]`、`suggested_queries: List[str]`
- `check_evidence_sufficiency(query: str, code_contents: List[str], guidelines: List[CodeGuideline]) -> EvidenceCheckResult`
  - **第一阶段：规则判断**（无 LLM，零延迟）
    - `code_contents` 为空 → 直接返回 `is_sufficient=False`
    - `len(guidelines) < 3` 且 query 含复杂特征词（调用链、完整流程、为什么、如何实现） → `is_sufficient=False`
    - 命中文件全在同一个文件内 且 query 暗示跨模块 → `is_sufficient=False`
  - **第二阶段：可选 LLM 判断**（仅在规则无法确定时触发，使用最小模型）
    - 仅对"规则无法确定"的中间情况使用，并设超时（2s 超时则默认充分）
  - `suggested_queries`：从 query 中提取关键标识符和特征词，作为补充 grep 的输入

**`app/services/chat_service.py`**

- 三个入口函数均插入充分性判断
- 在 `merge_retrieval_results()` 之后调用 `check_evidence_sufficiency()`
- 若 `not is_sufficient`，调用 `run_supplemental_retrieval()`（可直接在 chat_service 内实现为私有函数）
- 流式版本（`handle_chat_stream`）在补检索阶段可向前端发送一条 `progress` 事件（`"正在补充检索..."`），不阻塞流式输出

---

## 功能四：依赖图接入检索编排（第二优先级）

### 4.1 改造前后的问答行为差异

**改造前**

检索命中某个函数后，系统不会自动追踪它调用了谁、谁调用了它。对"完整实现流程"、"数据从哪里来"类问题，系统只能展示被直接检索到的那一层，答案在解释调用链时容易中断。

**改造后**

当首轮检索命中的 chunk 涉及函数定义时，系统自动从依赖图中扩展：

- 对"完整流程"类 query：追被命中函数的下游调用（callee），扩展一跳
- 对"谁会触发"类 query：追被命中函数的上游调用方（caller），扩展一跳

扩展出的函数所在 chunk 被补入 code_contents，最终一起送入上下文。Token 消耗由功能一的 chunk-aware 裁剪控制，不会无限膨胀。

### 4.2 改造后的完整流程

```
merge_retrieval_results() → 得到首轮 guidelines（含命中函数名）
    ↓
check_evidence_sufficiency()
    ↓
若 missing_aspects 含 "调用链" / "完整流程"，或首轮命中为函数类 chunk：
    [新增] expand_via_dependency_graph(guidelines, repo_id, direction, max_hops=1)
        - 从命中 chunk 中提取 symbol_name
        - 查 dependency_graph 中该 symbol 的 callee / caller
        - 对扩展出的 symbol 在 ChromaDB 中做精确 name 查询，补取对应 chunk
        - 返回补充 CodeGuideline 列表
    ↓
将扩展结果合并入 code_contents
    ↓
trim_chunks_to_budget()
    ↓
LLM 生成
```

### 4.3 需要修改的模块

| 模块 | 修改性质 |
|---|---|
| `app/services/dependency_graph.py` | 新增查询接口：按 symbol_name 查 callee / caller |
| `app/services/two_stage_retriever.py` | 新增 `expand_via_dependency_graph()` |
| `app/services/chat_service.py` | 在充分性判断后的补检索中调用依赖图扩展 |

### 4.4 架构层修改方案

**`app/services/dependency_graph.py`**

- 新增 `get_callees(repo_id: str, symbol_name: str, file_path: Optional[str] = None) -> List[str]`：返回 symbol 调用的函数名列表
- 新增 `get_callers(repo_id: str, symbol_name: str, file_path: Optional[str] = None) -> List[str]`：返回调用 symbol 的函数名列表
- **重名 symbol 消歧**：两个函数均接受 `file_path` 约束参数。当同一仓库中存在多个同名函数（如不同模块都有 `create()`、`handle()` 等），必须通过 `file_path` 限定来源文件确定唯一 symbol。`file_path` 来自调用方传入的 `CodeGuideline.file_path`，不允许只凭 `symbol_name` 做跨文件全局查询。若 `file_path` 为空则返回空列表（保守策略，优于返回多义结果）
- 底层基于已有的 AST 解析提取的 `calls` 字段，无需重新解析

**`app/services/two_stage_retriever.py`**

- 新增 `expand_via_dependency_graph(guidelines: List[CodeGuideline], repo_id: str, direction: str = "callee", max_hops: int = 1) -> List[CodeGuideline]`
  - 从 guidelines 中提取 `symbol_name` + `file_path`（两者均非空才处理，缺一则跳过）
  - 调用 `get_callees(repo_id, symbol_name, file_path)` 或 `get_callers()` 得到扩展 symbol 列表
  - 对每个扩展 symbol 调用已有的 `stage1_discovery` name 精确查询路径，补取对应 chunk
  - 去重后返回，最多扩展 `max_hops=1` 跳，每跳最多取 5 个 symbol（控制 token 膨胀）

**`app/services/chat_service.py`**

- 在 `run_supplemental_retrieval()` 中，当 `"调用链"` 在 `missing_aspects` 中时，调用 `expand_via_dependency_graph()`
- 调用链方向的选择：
  - query 含"怎么实现"/"流程"/"入口"→ `direction="callee"`
  - query 含"谁调用"/"被哪里触发"/"来源" → `direction="caller"`
  - 默认 `direction="callee"`

---

## 功能五：Planner 从文件级读取升级为定点读取（第二优先级）

### 5.1 改造前后的问答行为差异

**改造前**

当 planner 识别出目标文件后，`read_file_context(repo_id, file_path, 1, 300)` 直接读取文件前 300 行。这对"入口逻辑在文件头"的情况有效，但对以下情况失效：

- 关键函数位于文件中段（如第 400 行的 `handle_chat_stream`）
- 目标常量在文件末尾
- 文件超过 300 行但核心实现在后半段

**改造后**

planner 在返回文件路径的同时，也尝试返回目标 symbol 名或行范围。读取时基于 symbol 位置做窗口读取，而不是固定从第 1 行开始。

### 5.2 改造后的完整流程

```
is_broad_query() 或 evidence_checker 触发 planner：
    ↓
[升级] plan_retrieval() 返回 List[PlannedTarget]
    PlannedTarget:
        file_path: str
        symbol_name: Optional[str]   # 新增：planner 认为最相关的函数/类名
        hint_lines: Optional[str]    # 新增：如果能从索引中确定行范围
    ↓
[升级] read_targeted_context(repo_id, target: PlannedTarget) -> str
    - 若 symbol_name 存在：
        * 在 ChromaDB 中精确查该 symbol，取其 start_line / end_line
        * 调用 read_file_context(file_path, start_line - 10, end_line + 10)（含上下文缓冲）
    - 若无 symbol，fallback 到前 300 行（保持向后兼容）
```

### 5.3 需要修改的模块

| 模块 | 修改性质 |
|---|---|
| `app/services/retrieval_planner.py` | 升级输出结构：返回 `PlannedTarget`（含 symbol_name）而非纯文件路径 |
| `app/services/two_stage_retriever.py` | 新增 `read_targeted_context()`，替代固定行范围的 `read_file_context` 调用 |
| `app/services/chat_service.py` | 调用层适配：从 `planned_files[:3]` 改为 `planned_targets[:3]` |
| `app/schemas/` | 新增 `PlannedTarget` schema（或在 retrieval_planner.py 内定义 dataclass） |

### 5.4 架构层修改方案

**`app/services/retrieval_planner.py`**

- 新增 `PlannedTarget` dataclass：`file_path: str`、`symbol_name: Optional[str]`
- 修改 `PLANNER_PROMPT`：要求 LLM 同时返回文件路径和最相关的 symbol 名（JSON 格式从数组改为对象列表）
  ```json
  [{"file": "app/services/chat_service.py", "symbol": "handle_chat_stream"}]
  ```
- `plan_retrieval()` 返回类型从 `List[str]` 改为 `List[PlannedTarget]`
- 解析时兼容旧格式（纯字符串数组），向后兼容

**`app/services/two_stage_retriever.py`**

- 新增 `read_targeted_context(repo_id: str, target: PlannedTarget) -> str`
  - 若 `target.symbol_name` 存在，向 ChromaDB 查询该 symbol 的 `start_line`（已存储在 chunk metadata）
  - 取 `[start_line - 10, end_line + 30]` 窗口（函数体完整 + 少量上下文）
  - 若查询失败或无结果，退回 `read_file_context(repo_id, file_path, 1, 300)`

**`app/services/chat_service.py`**

- 三个入口函数中的 planner 调用段：
  - 将 `plan_retrieval()` 返回值由 `List[str]` 改为 `List[PlannedTarget]`
  - 将 `read_file_context(repo_id, fp, 1, 300)` 替换为 `read_targeted_context(repo_id, target)`

---

## 功能六：最小测试基线（第一优先级，Phase 0 护栏）

### 6.1 为什么测试基线是 Phase 0 而不是可选项

两份来源文档都明确指出：没有测试基线，后续所有升级都无法判断收益还是回退。尤其是功能一到五都涉及检索链路核心改动，任何一处引入 regression 都难以被快速发现。测试基线不是"有空再补"的工程卫生，而是整个升级路线的前置护栏。

### 6.2 需要锁定的最小行为集

以下行为必须被可执行、可回归的测试覆盖，作为升级过程中的变更检测器：

| 测试目标 | 验证内容 | 优先级 |
|---|---|---|
| `fuse_query()` 多轮追问重写 | 代词消解后的 fused_query 是否包含正确主体 | 最高 |
| `stage1_discovery()` 混合检索 | symbol 命中、node_type 补召回是否优于纯向量 | 最高 |
| `apply_token_budget()` / `trim_chunks_to_budget()` | 裁剪后所有保留 chunk 是否为完整单元（不存在半截内容） | 最高 |
| SSE 输出顺序 | `session_id → token → chunk_refs → done` 顺序稳定 | 高 |
| grep 搜索命中准确性 | 精确标识符查询能命中正确文件和行号 | 高（功能二完成后） |
| 证据充分性判断 | 空结果 / 复杂问题特征词触发 `is_sufficient=False` | 高（功能三完成后） |
| 降级路径标注 | context overflow 时输出明确标注"未参考代码上下文" | 中 |

### 6.3 需要修改的模块

| 模块 | 修改性质 |
|---|---|
| `tests/unit/test_token_budget.py` | 新建或补充：验证 chunk 边界完整性 |
| `tests/unit/test_query_fusion.py` | 新建：多轮追问重写行为验证 |
| `tests/unit/test_code_searcher.py` | 新建：grep / 路径搜索命中准确性（功能二后） |
| `tests/unit/test_evidence_checker.py` | 新建：充分性判断规则覆盖（功能三后） |
| `tests/integration/test_chat_api.py` | 取消 skip，补充 SSE 顺序 + session 基础流程断言 |

### 6.4 架构层修改方案

测试基线不引入新的生产代码。要求：

- 所有单元测试针对纯函数（`fuse_query`、`trim_chunks_to_budget`、`check_evidence_sufficiency`），不依赖外部服务，可离线运行
- `tests/integration/test_chat_api.py` 中的 session 流程测试允许使用 mock ChromaDB + mock LLM，但 SSE 事件顺序断言必须真实触发流式 handler
- 每个功能实施完成后，对应测试必须同步补充，不允许功能合并但测试延后

---

## 功能七：轻量 Rerank（后续阶段占位）

### 7.1 在路线图中的定位

rerank 的价值已在来源文档中确认：当前问题不是"完全召不回"，而是"召回后前若干排得不够准"。但其优先级低于修复 token budget、补 grep 层、建立测试基线。原因是：

1. 没有测试基线，rerank 的收益无法量化
2. grep 层补齐后，精确命中类问题会大幅改善，rerank 的边际收益需要重新评估
3. 项目存在流式 chat，重模型 rerank 会引入明显时延

因此 rerank 在本策略文档中作为**明确的后续阶段任务**存在，而不是被忽略。

### 7.2 规划中的接入点

接入位置：`merge_retrieval_results()` 之后、`stage2_assembly()` 之前。

```
merge_retrieval_results()  →  top-20 候选
    ↓
[占位] rerank(candidates, query) → top-8~10 重排结果
    ↓
stage2_assembly()
```

### 7.3 实施顺序规划

| 阶段 | 内容 |
|---|---|
| Phase 2 之后 | 规则型 rerank：symbol 精确命中加权、同文件重复 chunk 降权、文件多样性奖励 |
| 有评测基线后 | 评估是否引入小模型 rerank（如 cross-encoder）|
| 按需 | 独立重排模型，仅在规则型 rerank 收益不足时考虑 |

### 7.4 前置条件

实施 rerank 前需满足：功能六测试基线已建立，且 grep 层（功能二）已上线至少一个版本，有可量化的检索质量对比数据。

---

## 功能八：Research State / Retrieval Memory（后续阶段占位）

### 8.1 在路线图中的定位

research_state 是将系统从"增强版单次 RAG 问答"升级为"证据驱动的代码研究流程"的关键环节。来源文档明确指出：当前多轮对话的问题不是"没有历史记录"，而是记录的是自然语言对话，而不是研究状态。

多轮追问时系统每次都像"重新开始看问题"，而不是"沿上轮研究继续推进"。

research_state 的实现可以后置，但**不能在策略文档中缺席**，否则"证据驱动的研究流程"这条升级主线就被讲窄了。

### 8.2 规划中的数据结构

在 Redis 会话中新增 `research_state` 字段，与现有 `messages` 并列存储：

```json
{
  "confirmed_files": ["app/services/chat_service.py"],
  "confirmed_symbols": ["handle_chat", "stage1_discovery"],
  "confirmed_claims": ["普通 chat 只有单轮检索"],
  "open_questions": ["文件级搜索层是否存在"],
  "searched_queries": ["chat context loss"],
  "key_citations": [
    {"file": "app/services/chat_service.py", "start": 180, "end": 214}
  ]
}
```

### 8.3 规划中的接入点

| 环节 | 变更 |
|---|---|
| `append_turn()` | 同步更新 `research_state`（补充 confirmed_files / confirmed_symbols） |
| `handle_chat()` 入口 | 读取 `research_state`，对追问问题优先尝试局部检索而非全局召回 |
| `check_evidence_sufficiency()` | 将 `open_questions` 作为输入，帮助判断当前问题是否延续上轮缺口 |
| Deep Research | 非最终轮结论写入 `open_questions`，最终轮写入 `confirmed_claims` |

### 8.4 实施时机

在功能三（二轮检索）上线并稳定后再开始。原因：
- 二轮检索已经能缓解大部分单轮证据不足问题
- research_state 需要二轮检索提供足够的"补证据信号"才能积累有效状态
- 过早引入 research_state 结构，若检索本身不稳，状态质量也无法保证

---

## 各功能依赖关系与实施顺序

```
功能六（测试基线）      ←── Phase 0，所有升级的护栏
    ↓
功能一（Token Budget）  ←── Phase 1，独立，最先修复
功能五（定点读取）      ←── Phase 1，独立，改动范围小
    ↓
功能二（Grep 层）       ←── Phase 2，新增搜索层，不改现有逻辑
功能七（Rerank 占位）   ←── Phase 2 之后评估
    ↓
功能三（二轮检索）      ←── Phase 3，依赖功能二
功能四（依赖图扩展）    ←── Phase 3，集成进功能三的补检索流程
    ↓
功能八（Research State）←── Phase 4，依赖功能三稳定后
```

### 推荐实施顺序

| 阶段 | 功能 | 理由 |
|---|---|---|
| Phase 0 | 功能六：测试基线 | 升级护栏，无基线则后续无法判断收益 |
| Phase 1 | 功能一：Token Budget | 修复数据损坏问题，风险低，收益直接 |
| Phase 1 | 功能五：定点读取 | 改动范围小，独立，planner 精度立即提升 |
| Phase 2 | 功能二：Grep 层 | 补结构性缺口，不改动现有逻辑 |
| Phase 2+ | 功能七：Rerank（占位） | 有评测数据后决定是否推进 |
| Phase 3 | 功能三：二轮检索 | 依赖功能二完成 |
| Phase 3 | 功能四：依赖图扩展 | 集成进功能三补检索流程 |
| Phase 4 | 功能八：Research State | 依赖功能三稳定后推进 |

---

## 附：新增文件清单

| 文件 | 说明 |
|---|---|
| `app/services/code_searcher.py` | 路径搜索 + grep 搜索，纯工具函数 |
| `app/services/evidence_checker.py` | 证据充分性判断，返回 EvidenceCheckResult |
| `tests/unit/test_token_budget.py` | chunk 边界完整性单元测试 |
| `tests/unit/test_query_fusion.py` | 多轮追问重写行为验证 |
| `tests/unit/test_code_searcher.py` | grep / 路径搜索命中准确性（功能二后补） |
| `tests/unit/test_evidence_checker.py` | 充分性判断规则覆盖（功能三后补） |

## 附：修改文件清单

| 文件 | 涉及功能 |
|---|---|
| `app/services/token_budget.py` | 功能一 |
| `app/services/two_stage_retriever.py` | 功能二、四、五、七（rerank 接入点） |
| `app/services/chat_service.py` | 功能一、二、三、四、五（调用层适配） |
| `app/services/retrieval_planner.py` | 功能五 |
| `app/services/dependency_graph.py` | 功能四 |
| `app/services/conversation_memory.py` | 功能八（research_state 存储） |
| `app/schemas/chunk_node.py` 或相关 schema | 功能二（source 字段）、功能五（PlannedTarget） |
| `tests/integration/test_chat_api.py` | 功能六（取消 skip，补充断言） |
