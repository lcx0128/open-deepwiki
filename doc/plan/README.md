# RAG / AI Chat 升级实施计划

> 生成时间：2026-04-21
> 来源：`doc/RAG_UPGRADE_STRATEGY_2026-04-21.md`
> 用途：作为 Agent 逐步实现的上下文依据

---

## 计划总览

本目录包含 7 份实施计划文档，按实施顺序排列。每份文档对应一个 Agent 可在单次会话中完成的独立任务单元。

```
Phase 0 ─── Plan 00: 测试基线（护栏）
               ↓
Phase 1 ─┬─ Plan 01: Token Budget Chunk-aware 裁剪
         └─ Plan 02: Planner 定点读取升级
               ↓                ↓
Phase 2 ─┬─ Plan 03: Code Searcher 模块（新增，独立）
         └─ Plan 04: 三路搜索集成（依赖 01+02+03）
               ↓
Phase 3 ─┬─ Plan 05: 证据充分性判断 + 自动二轮检索
         └─ Plan 06: 依赖图接入检索编排
```

---

## 各 Plan 摘要

| Plan | 文件 | Phase | 前置依赖 | 核心改动 |
|---|---|---|---|---|
| 00 | [PLAN_00_TEST_BASELINE.md](PLAN_00_TEST_BASELINE.md) | 0 | 无 | 补充测试基线，锁定现有行为 |
| 01 | [PLAN_01_TOKEN_BUDGET_CHUNK_AWARE.md](PLAN_01_TOKEN_BUDGET_CHUNK_AWARE.md) | 1 | Plan 00 | 字符截断 → chunk 整块裁剪 |
| 02 | [PLAN_02_PLANNER_TARGETED_READ.md](PLAN_02_PLANNER_TARGETED_READ.md) | 1 | Plan 00 | 文件前 300 行 → 基于 symbol 定点读取 |
| 03 | [PLAN_03_CODE_SEARCHER.md](PLAN_03_CODE_SEARCHER.md) | 2 | Plan 00 | 新增路径搜索 + Grep 搜索模块 |
| 04 | [PLAN_04_THREE_WAY_SEARCH_INTEGRATION.md](PLAN_04_THREE_WAY_SEARCH_INTEGRATION.md) | 2 | Plan 01, 02, 03 | 三路并行检索 + 结果合并 |
| 05 | [PLAN_05_EVIDENCE_CHECKER.md](PLAN_05_EVIDENCE_CHECKER.md) | 3 | Plan 03, 04 | 证据充分性判断 + 自动补检索 |
| 06 | [PLAN_06_DEPENDENCY_GRAPH_EXPANSION.md](PLAN_06_DEPENDENCY_GRAPH_EXPANSION.md) | 3 | Plan 05 | 依赖图一跳扩展接入检索 |

---

## 依赖关系

```
Plan 00 ──→ Plan 01 ──→ Plan 04
         ├→ Plan 02 ──────↗  ↑
         └→ Plan 03 ─────────┘──→ Plan 05 ──→ Plan 06
```

- **Plan 00** 是所有 Plan 的前置（测试护栏）
- **Plan 01** 和 **Plan 02** 可并行（均只依赖 Plan 00）
- **Plan 03** 独立创建新模块，不修改现有生产代码（可与 Plan 01/02 并行）
- **Plan 04** 依赖 Plan 01（chunk 裁剪）、Plan 02（planner 返回 PlannedTarget）、Plan 03（code_searcher 模块）。Plan 02 修改了 `plan_retrieval()` 返回类型和 `chat_service.py` 的 planner 调用段，Plan 04 在同一文件的相邻区域做三路检索改造，必须在 Plan 02 完成后基于其代码状态实施
- **Plan 05** 依赖 Plan 04（三路搜索已集成）
- **Plan 06** 依赖 Plan 05（补检索流程已就绪）

---

## 未包含在本次实施计划中的功能

以下功能在策略文档中作为**后续阶段占位**，不在本次 Plan 范围内：

| 功能 | 策略文档编号 | 原因 |
|---|---|---|
| 轻量 Rerank | 功能七 | 需要功能二（Grep 层）上线后有数据再评估 |
| Research State / Retrieval Memory | 功能八 | 需要功能三（二轮检索）稳定后推进 |

---

## Agent 实施指南

1. **每次只实施一个 Plan**：不跨 Plan 工作，避免注意力分散
2. **先读 Plan 再读源码**：Plan 中已包含精确到行号的改动位置
3. **严格遵循前置依赖**：如 Plan 04 的实施必须在 Plan 01、Plan 02 和 Plan 03 全部完成后
4. **测试先行**：每个 Plan 的测试要求必须满足才能视为完成
5. **commit 粒度**：每个 Plan 完成后独立 commit
6. **最终 Review**：所有 Plan 完成后，由独立 Agent 执行全局 Review，检查跨模块一致性
