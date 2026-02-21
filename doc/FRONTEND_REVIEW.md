# Open-DeepWiki 前端（模块四）代码审查报告 v2

> **审查日期**: 2026-02-21（第二轮深度审查）
> **审查范围**: `frontend/src/` 全部 Vue 3 前端文件 + 测试文件（Module 4）
> **审查文件数**: 30
> **审查方法**: 逐文件阅读 + 后端 API schema 交叉比对 + SSE 事件 payload 验证
> **总发现问题数**: 13（CRITICAL: 1, WARNING: 6, INFO: 6）

---

## 一、概要评估

前端代码整体质量较高，架构清晰，符合 Vue 3 Composition API + Pinia + TypeScript 的最佳实践。
API 层接口定义与后端 Pydantic schema 对齐良好，路由命名一致，Store 方法调用完整。

主要风险集中在以下几个方面：
1. **XSS 漏洞**：`MarkdownView.vue` 使用 `v-html` 渲染未经消毒的 Markdown HTML
2. **SSE 字段缺失**：SSE 事件中从不发送 `files_total`/`files_processed`，前端读取永远为 `undefined`
3. **ProgressBar 阶段定位逻辑错误**：`pending`/`failed` 状态下阶段指示器显示不正确
4. **Mermaid 主题切换失效**：初始化后无法响应暗色/亮色主题切换

---

## 二、CRITICAL 级别问题（必须修复）

### [CRITICAL-1] XSS 漏洞：`v-html` 渲染未消毒的 Markdown 内容

**文件**: `frontend/src/components/MarkdownView.vue` 第 51 行

```html
<div v-if="block.type === 'html'" class="markdown-body" v-html="block.content" />
```

**问题**: `marked.parse()` 将 Markdown 转换为 HTML 后直接通过 `v-html` 插入 DOM，未经任何 XSS 消毒处理。Wiki 内容来自 LLM 生成，LLM 生成的 Markdown 可能包含恶意 `<script>` 标签、`onerror` 事件处理器、或 `javascript:` 协议链接。攻击者也可通过构造特殊代码仓库内容（如在注释中嵌入 HTML）来间接注入恶意代码，LLM 总结时可能原样输出。

**影响**: 任何能触发 Wiki 生成的用户都可能间接导致 XSS 攻击，窃取其他用户的会话信息或执行任意操作。

**建议修复**: 安装 `dompurify`（`npm install dompurify @types/dompurify`），在 `marked.parse()` 输出后过滤：

```typescript
import DOMPurify from 'dompurify'

const html = DOMPurify.sanitize(
  marked.parse(part, { breaks: true, gfm: true }) as string
)
```

---

## 三、WARNING 级别问题（应当修复）

### [WARNING-1] SSE 事件从不包含 `files_total` / `files_processed` 字段

**文件**: `frontend/src/composables/useEventSource.ts` 第 37-38 行

```typescript
filesTotal: data.files_total ?? taskStore.currentTask?.filesTotal,
filesProcessed: data.files_processed ?? taskStore.currentTask?.filesProcessed,
```

**后端 `_publish()` 函数**（`app/tasks/process_repo.py` 第 286-299 行）发送的 SSE payload：

```python
data = {
    "status": status,
    "progress_pct": round(progress, 1),
    "stage": stage,
    "timestamp": ...,
    **extra,  # 只有 wiki_id 通过 extra 传入，从未传入 files_total/files_processed
}
```

**问题**: 后端 SSE 事件**从未发送** `files_total` 或 `files_processed`。前端 `data.files_total` 和 `data.files_processed` 永远是 `undefined`，`??` 运算符回退到 store 中的旧值。由于初始值设为 `0`，进度条中的文件计数将始终显示 `0/0`，不会实时更新。

**建议修复**:
- 方案 A（推荐）: 后端在解析阶段的 `_publish` 调用中通过 `**extra` 传入文件计数信息
- 方案 B: 前端在 SSE 连接期间定期轮询 `GET /api/tasks/{id}` 获取文件计数

### [WARNING-2] ProgressBar 阶段索引在 `pending`/`failed` 状态下定位错误

**文件**: `frontend/src/components/ProgressBar.vue` 第 21-24 行

```typescript
const currentStageIndex = computed(() => {
  const idx = stages.findIndex(s => s.key === props.status)
  return idx >= 0 ? idx : 0
})
```

**问题**: `stages` 数组包含 `['cloning', 'parsing', 'embedding', 'generating', 'completed']`。当 `status` 为 `'pending'`、`'failed'` 或 `'cancelled'` 时，`findIndex` 返回 `-1`，回退到 `0`。

- `status === 'pending'` 时：错误地高亮 `cloning` 阶段（用户会误以为已开始克隆）
- `status === 'failed'` 时：错误地高亮 `cloning` 阶段（应停留在实际失败的阶段）

**建议修复**: 对 `pending` 返回 `-1`（无高亮），对 `failed` 使用 `props.currentStage` 或后端 `failed_at_stage` 来确定失败位置：

```typescript
const currentStageIndex = computed(() => {
  if (props.status === 'pending') return -1
  const idx = stages.findIndex(s => s.key === props.status)
  return idx >= 0 ? idx : stages.findIndex(s => s.key === props.currentStage)
})
```

### [WARNING-3] 前端 `TaskStatusResponse` 类型与后端 schema 不匹配

**文件**: `frontend/src/api/repositories.ts` 第 42-43 行

```typescript
files_total: number | null
files_processed: number | null
```

**后端 schema**（`app/schemas/repository.py` 第 53-54 行）：

```python
files_total: int          # 非 Optional，默认值 0
files_processed: int      # 非 Optional，默认值 0
```

**问题**: 后端定义为 `int`（不可为 null），前端定义为 `number | null`。类型不准确可能误导开发者添加不必要的 null 检查。

同样，前端 `TaskStatusResponse` 缺少后端存在的 `failed_at_stage` 字段。

**建议修复**:

```typescript
files_total: number        // 去掉 | null
files_processed: number    // 去掉 | null
failed_at_stage: string | null  // 新增，与后端对齐
```

### [WARNING-4] `WikiView.vue` 和 `RepoListView.vue` 使用 `router.push` 违反设计约束

**文件**: `frontend/src/views/WikiView.vue` 第 71 行

```typescript
router.push({ path: '/', query: { taskId: result.task_id } })
```

**文件**: `frontend/src/views/RepoListView.vue` 第 71 行

```typescript
router.push({ path: '/', query: { taskId: result.task_id } })
```

**问题**: CLAUDE.md 中的设计约束明确要求：

> "After task submission, use `history.pushState(null, '', '?taskId=xxx')` -- do NOT use Vue Router navigation."

`HomeView.vue` 在提交后正确使用了 `history.pushState`，但 `WikiView.vue` 和 `RepoListView.vue` 在触发 reprocess/regenerate 后使用了 `router.push`。这会触发完整的 Vue Router 导航和组件重新挂载，行为不一致。

**建议修复**: 统一方案。如果意图是跳转到首页查看进度，使用 `router.push` 是合理的（因为需要切换视图）。但 `HomeView.vue` 内部的 URL 更新应统一使用同一种方式。

### [WARNING-5] `useMermaid.ts` 模块级变量阻止主题切换

**文件**: `frontend/src/composables/useMermaid.ts` 第 4、10 行

```typescript
let mermaidInitialized = false

function initMermaid(isDark = false) {
  if (mermaidInitialized) return  // 第二次调用直接返回
  // ...
  mermaidInitialized = true
}
```

**问题**: `mermaidInitialized` 是模块级变量，一旦设为 `true` 就永远不会再初始化 Mermaid。当用户通过 `AppHeader.vue` 切换暗色/亮色主题时，`initMermaid(isDark)` 不会生效，Mermaid 图表将始终保持首次初始化时的主题。

**建议修复**: 去掉提前退出逻辑，或提供独立的 `reinitMermaid(isDark)` 方法：

```typescript
function initMermaid(isDark = false) {
  mermaid.initialize({
    startOnLoad: false,
    theme: isDark ? 'dark' : 'default',
    // ...
  })
  mermaidInitialized = true
  isInitialized.value = true
}
```

### [WARNING-6] `AppHeader.vue` 主题状态未持久化，未与 Mermaid 同步

**文件**: `frontend/src/components/AppHeader.vue` 第 4-9 行

```typescript
const isDark = ref(false)

function toggleTheme() {
  isDark.value = !isDark.value
  document.documentElement.setAttribute('data-theme', isDark.value ? 'dark' : 'light')
}
```

**问题**:
1. `isDark` 是组件局部状态，页面刷新后丢失，不持久化到 `localStorage`
2. 主题切换后不通知 Mermaid 重新初始化，已渲染的 SVG 图表颜色不会随主题变化
3. 其他组件无法感知当前主题状态

**建议修复**:
- 将主题状态提升到 Pinia store 或 `provide/inject`
- 使用 `localStorage` 持久化主题偏好
- 主题切换时触发 Mermaid 重新渲染

---

## 四、INFO 级别问题（建议改进）

### [INFO-1] `marked.parse()` 返回类型断言不安全

**文件**: `frontend/src/components/MarkdownView.vue` 第 32 行

```typescript
const html = marked.parse(part, { breaks: true, gfm: true }) as string
```

**问题**: `marked.parse()` 同步调用时返回 `string | Promise<string>`，使用 `as string` 跳过类型检查。较安全的方式是明确 `{ async: false }` 或使用 `marked.parseInline`。

### [INFO-2] `highlight.js` 在 `package.json` 中声明但未在任何文件中使用

**文件**: `frontend/package.json` 第 21 行

```json
"highlight.js": "^11.9.0"
```

**问题**: 没有任何源文件导入或配置 `highlight.js`。如需代码高亮，应在 `MarkdownView.vue` 中配置 `marked` 的 highlight 回调；如不需要，应移除以减小包体积。

### [INFO-3] SSE 重连成功后未重置计数器

**文件**: `frontend/src/composables/useEventSource.ts` 第 62-72 行

```typescript
eventSource.onerror = () => {
  // ...
  if (reconnectAttempts.value < MAX_RECONNECT_ATTEMPTS) {
    reconnectAttempts.value++
    // ...
  }
}
```

**问题**: 当 SSE 断线后成功重连并收到消息时，`reconnectAttempts` 不会重置为 `0`。间歇性断开会累计重连次数，最终超过上限后不再重连。

**建议**: 在 `eventSource.onmessage` 成功收到消息时添加 `reconnectAttempts.value = 0`。

### [INFO-4] `WikiView.vue` TOC 链接不可导航

**文件**: `frontend/src/views/WikiView.vue` 第 201-208 行

```html
<a v-for="item in tocItems" :key="item.id" class="toc__item"
   href="#" @click.prevent>{{ item.text }}</a>
```

**问题**: TOC 目录项使用 `@click.prevent` 阻止了默认行为，但没有实现任何滚动逻辑。点击目录项不会跳转到对应标题。

**建议**: 为 Markdown 渲染的标题生成锚点 ID，在 `@click` 中实现 `scrollIntoView()`。

### [INFO-5] `useEventSource.ts` 中 `failed` 状态使用 `stage` 字段作为错误信息

**文件**: `frontend/src/composables/useEventSource.ts` 第 53 行

```typescript
taskStore.updateProgress({ errorMsg: data.stage || '处理失败' })
```

**问题**: 后端 SSE 的 `stage` 字段在 `failed` 状态时包含的是如 `"处理失败: 连接超时"` 这样的文本。虽然可用，但语义上 `stage` 应该是阶段名称而非错误描述。如果后端未来增加专用的 `error_msg` 字段，前端应优先使用。

### [INFO-6] `HomeView.vue` 中 409 冲突时 `existing_task_id` 字段访问

**文件**: `frontend/src/views/HomeView.vue` 第 124 行

```typescript
const existingTaskId = error.response.data?.existing_task_id
```

**问题**: 经过后端代码验证，409 响应的 `detail` 字段实际上是一个对象，包含 `existing_task_id`（`app/api/repositories.py` 第 66-71 行）。但 FastAPI 的 `HTTPException` 将 `detail` 作为响应体返回，前端需要通过 `error.response.data?.detail?.existing_task_id` 访问（多一层 `detail`）。当前写法 `error.response.data?.existing_task_id` 会得到 `undefined`。

**建议修复**:

```typescript
const existingTaskId = error.response.data?.detail?.existing_task_id
```

---

## 五、验证通过项

以下方面经过逐一检查，确认正确无误：

### 5.1 API 接口字段对齐（后端 snake_case）
| 前端接口 | 后端 Schema | 字段对齐 |
|----------|-------------|----------|
| `SubmitRepoRequest` (`url`, `pat_token`, `branch`, `llm_provider`, `llm_model`) | `RepositoryCreateRequest` | 完全匹配 |
| `SubmitRepoResponse` (`task_id`, `repo_id`, `status`, `message`) | `RepositoryCreateResponse` | 完全匹配 |
| `TaskStatusResponse` (`id`, `repo_id`, `type`, `status`, `progress_pct`, `current_stage`, `error_msg`, `created_at`, `updated_at`) | `TaskStatusResponse` | 匹配（缺少 `failed_at_stage`，见 WARNING-3）|
| `WikiResponse` / `WikiSection` / `WikiPage` | `WikiResponse` / `WikiSectionResponse` / `WikiPageResponse` | 完全匹配 |
| `RegenerateWikiResponse` (`task_id`, `message`) | `WikiRegenerateResponse` | 完全匹配 |
| `RepositoryItem` (`id`, `url`, `name`, `platform`, `status`, `last_synced_at`, `created_at`) | `RepositoryListItem` | 完全匹配 |
| `RepositoryListResponse` (`items`, `total`, `page`, `per_page`) | `RepositoryListResponse` | 完全匹配 |

### 5.2 路由名称一致性
- `{ name: 'home' }` -- `WikiView.vue:130` 正确
- `{ name: 'wiki', params: { repoId } }` -- `HomeView.vue:261`, `RepoListView.vue:184`, `ChatView.vue:99` 正确
- `{ name: 'chat', params: { repoId } }` -- `WikiView.vue:152`, `RepoListView.vue:191` 正确
- `{ name: 'repos' }` -- `WikiView.vue:95` 正确
- 所有路由名称与 `router/index.ts` 中定义完全一致

### 5.3 Store 方法调用
- `taskStore.setTask()` / `updateProgress()` / `clearTask()` / `pushToHistory()` -- 全部存在且调用正确
- `wikiStore.setWiki()` / `setActivePage()` / `clearWiki()` -- 全部存在且调用正确
- `chatStore.addMessage()` / `appendToLastAssistant()` / `updateLastAssistant()` / `setLastAssistantRefs()` / `finishStreaming()` / `clearChat()` -- 全部存在且调用正确
- `repoStore.setRepos()` / `removeRepo()` / `updateRepoStatus()` / `clearRepos()` -- 全部存在且调用正确

### 5.4 SSE 核心字段映射
| SSE 字段 | Store 字段 | 状态 |
|----------|-----------|------|
| `data.status` | `status` | 正确 |
| `data.progress_pct` | `progressPct` | 正确 |
| `data.stage` | `currentStage` | 正确 |
| `data.wiki_id` | `wikiId` | 正确 |
| `data.files_total` | `filesTotal` | 后端不发送（见 WARNING-1）|
| `data.files_processed` | `filesProcessed` | 后端不发送（见 WARNING-1）|

### 5.5 HomeView snake_case 到 camelCase 映射
- `task.repo_id` -> `repoId` / `task.progress_pct` -> `progressPct` / `task.current_stage` -> `currentStage` 等全部映射正确

### 5.6 Vue 3 Composition API 规范
- 所有组件使用 `<script setup lang="ts">` -- 正确
- `defineProps` / `defineEmits` 使用泛型语法 -- 正确
- `computed` / `ref` / `watch` / `onMounted` / `onUnmounted` 使用正确
- Pinia store 使用 `defineStore` + Composition API 风格 -- 正确

### 5.7 配置
- Vite 开发代理 `/api` -> `http://localhost:8000` -- 正确
- `@` 路径别名指向 `./src` -- 正确
- `mermaid` 加入 `optimizeDeps.include` -- 正确
- `mermaid.initialize({ startOnLoad: false })` -- 正确

### 5.8 测试
- `tests/setup.ts` 正确 mock 了 `EventSource` 和 Pinia
- task / wiki / chat Store 测试覆盖核心方法和边界条件
- ProgressBar / StatusBadge 组件测试覆盖所有状态变体
- 测试数据字段与 Store / API 接口定义一致

---

## 六、问题汇总

| 序号 | 级别 | 文件 | 行号 | 问题摘要 |
|------|------|------|------|----------|
| 1 | CRITICAL | `MarkdownView.vue` | 51 | `v-html` 渲染未消毒 HTML，XSS 漏洞 |
| 2 | WARNING | `useEventSource.ts` | 37-38 | SSE 事件不含 `files_total`/`files_processed` |
| 3 | WARNING | `ProgressBar.vue` | 21-24 | `pending`/`failed` 状态下阶段索引定位错误 |
| 4 | WARNING | `repositories.ts` | 42-43 | 类型定义与后端不匹配，缺少 `failed_at_stage` |
| 5 | WARNING | `WikiView.vue` / `RepoListView.vue` | 71 | `router.push` 与 `history.pushState` 约束不一致 |
| 6 | WARNING | `useMermaid.ts` | 4, 10 | 初始化锁阻止主题切换 |
| 7 | WARNING | `AppHeader.vue` | 4-9 | 主题状态未持久化，未与 Mermaid 同步 |
| 8 | INFO | `MarkdownView.vue` | 32 | `marked.parse()` 类型断言不安全 |
| 9 | INFO | `package.json` | 21 | `highlight.js` 依赖未使用 |
| 10 | INFO | `useEventSource.ts` | 62-72 | 重连成功后未重置计数器 |
| 11 | INFO | `WikiView.vue` | 201-208 | TOC 链接无导航功能 |
| 12 | INFO | `useEventSource.ts` | 53 | `stage` 字段语义用作错误信息 |
| 13 | INFO | `HomeView.vue` | 124 | `existing_task_id` 访问路径缺少 `detail` 层 |

---

## 七、结论

### 整体就绪度

前端 Module 4 代码质量整体较高，架构清晰，与后端 API 对齐良好。

**CRITICAL 问题（1 个）**: `MarkdownView.vue` 的 XSS 漏洞是上线前必须修复的安全问题。需引入 `dompurify` 对 Markdown 渲染输出进行消毒。

**WARNING 问题（6 个）**: SSE 文件计数缺失影响用户体验（但不影响核心功能）；ProgressBar 阶段指示器在边界状态下视觉误导；Mermaid 主题切换失效。

### 优先级建议

1. **P0（必须修复）**: CRITICAL-1（XSS）
2. **P1（应当修复）**: WARNING-1（SSE 文件计数）、WARNING-2（ProgressBar 阶段定位）、WARNING-3（类型对齐）
3. **P2（可后续迭代）**: WARNING-4~6、全部 INFO 级别问题

### 审查结论

**REQUEST CHANGES** -- 存在 1 个 CRITICAL 级别的 XSS 安全漏洞，必须修复后才能合并。修复 CRITICAL-1 和 WARNING-1~3 后可进入集成测试阶段。
