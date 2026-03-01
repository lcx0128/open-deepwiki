<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import {
  getSystemConfig, updateSystemConfig,
  getSystemHealth, getSystemTasks, cancelTask,
  getStorageStats, scanCleanup, executeCleanup,
  testLlmConnection,
  type SystemConfig, type HealthResponse, type TaskItem, type TaskListResponse,
  type StorageResponse, type CleanupScanResponse,
  type TestConnectionRequest, type TestConnectionResponse,
} from '@/api/system'

// ── Tab state ────────────────────────────────────────────────────────────────
const activeTab = ref<'llm' | 'health' | 'tasks' | 'storage'>('llm')

// ══════════════════════════════════════════════════════════════════════════════
// Tab 1: LLM 配置
// ══════════════════════════════════════════════════════════════════════════════
const configLoading = ref(false)
const configSaving = ref(false)
const configSavedMsg = ref(false)
const configError = ref('')
const showEmbedding = ref(false)
const isFirstTime = ref(false)  // true when system_config.json has no overrides yet
const showPasswords = ref<Record<string, boolean>>({})  // keyed by field id

// Connection test state per provider key
const testResults = ref<Record<string, { loading: boolean; result: TestConnectionResponse | null }>>({})
const testModels = ref<Record<string, string>>({
  openai: 'gpt-5',
  dashscope: 'qwen-plus',
  gemini: 'gemini-3-flash-preview',
  custom: '',
})

async function testConnection(provider: string, apiKey: string, baseUrl: string, model: string) {
  const key = provider
  testResults.value[key] = { loading: true, result: null }
  try {
    const req: TestConnectionRequest = {
      provider,
      api_key: apiKey || undefined,
      base_url: baseUrl || undefined,
      model: model || undefined,
    }
    const result = await testLlmConnection(req)
    testResults.value[key] = { loading: false, result }
  } catch {
    testResults.value[key] = { loading: false, result: { success: false, latency_ms: null, error: '请求失败' } }
  }
}

function togglePassword(fieldId: string) {
  showPasswords.value[fieldId] = !showPasswords.value[fieldId]
}

function isKeyEmpty(value: string): boolean {
  return !value || value === ''
}

const llmForm = ref({
  default_provider: 'openai',
  default_model: '',
  openai_api_key: '',
  openai_base_url: '',
  dashscope_api_key: '',
  google_api_key: '',
  custom_base_url: '',
  custom_api_key: '',
})
const embeddingForm = ref({
  api_key: '',
  base_url: '',
  model: '',
})
const wikiLanguage = ref('Chinese')

async function loadConfig() {
  configLoading.value = true
  configError.value = ''
  try {
    const data = await getSystemConfig()
    llmForm.value = { ...data.llm }
    embeddingForm.value = { ...data.embedding }
    wikiLanguage.value = data.wiki_language || 'Chinese'
    isFirstTime.value = data.is_customized === false
  } catch {
    configError.value = '加载配置失败，请检查后端服务'
  } finally {
    configLoading.value = false
  }
}

async function saveConfig() {
  configSaving.value = true
  configError.value = ''
  try {
    const payload: Partial<SystemConfig> = {
      llm: { ...llmForm.value },
      embedding: { ...embeddingForm.value },
      wiki_language: wikiLanguage.value,
    }
    await updateSystemConfig(payload)
    configSavedMsg.value = true
    setTimeout(() => { configSavedMsg.value = false }, 3000)
  } catch {
    configError.value = '保存失败，请检查后端服务'
  } finally {
    configSaving.value = false
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Tab 2: 系统健康
// ══════════════════════════════════════════════════════════════════════════════
const healthData = ref<HealthResponse | null>(null)
const healthLoading = ref(false)
const healthError = ref('')

async function loadHealth() {
  healthLoading.value = true
  healthError.value = ''
  try {
    healthData.value = await getSystemHealth()
  } catch {
    healthError.value = '获取健康状态失败'
  } finally {
    healthLoading.value = false
  }
}

function serviceStatusLabel(status: string): string {
  const map: Record<string, string> = {
    ok: '正常',
    error: '异常',
    offline: '离线',
    unknown: '未知',
  }
  return map[status] ?? '未知'
}

// ══════════════════════════════════════════════════════════════════════════════
// Tab 3: 任务管理
// ══════════════════════════════════════════════════════════════════════════════
const tasksData = ref<TaskListResponse | null>(null)
const tasksLoading = ref(false)
const tasksError = ref('')
const taskFilter = ref('')
const taskPage = ref(1)
const taskPerPage = 15
const cancellingTask = ref<string | null>(null)

async function loadTasks() {
  tasksLoading.value = true
  tasksError.value = ''
  try {
    tasksData.value = await getSystemTasks(taskPage.value, taskPerPage, taskFilter.value || undefined)
  } catch {
    tasksError.value = '获取任务列表失败'
  } finally {
    tasksLoading.value = false
  }
}

async function handleCancelTask(taskId: string) {
  cancellingTask.value = taskId
  try {
    await cancelTask(taskId)
    await loadTasks()
  } catch {
    tasksError.value = '取消任务失败'
  } finally {
    cancellingTask.value = null
  }
}

function taskTypeLabel(type: string): string {
  const map: Record<string, string> = {
    full_process: '全量处理',
    incremental_sync: '增量同步',
    wiki_regenerate: 'Wiki重生成',
  }
  return map[type] ?? type
}

function taskStatusLabel(status: string): string {
  const map: Record<string, string> = {
    pending: '待处理',
    running: '运行中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
    interrupted: '已中断',
    cloning: '克隆中',
    parsing: '解析中',
    embedding: '向量化',
    generating: '生成中',
    syncing: '同步中',
  }
  return map[status] ?? status
}

function isTaskActive(status: string): boolean {
  return ['pending', 'running', 'cloning', 'parsing', 'embedding', 'generating', 'syncing'].includes(status)
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return '—'
  const normalized = /Z|[+-]\d{2}:\d{2}$/.test(dateStr) ? dateStr : dateStr + 'Z'
  return new Date(normalized).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const taskStatusFilters = [
  { value: '', label: '全部' },
  { value: 'running', label: '运行中' },
  { value: 'completed', label: '已完成' },
  { value: 'failed', label: '失败' },
  { value: 'cancelled', label: '已取消' },
  { value: 'interrupted', label: '已中断' },
]

watch(taskFilter, () => {
  taskPage.value = 1
  loadTasks()
})

watch(taskPage, loadTasks)

// ══════════════════════════════════════════════════════════════════════════════
// Tab 4: 存储与清理
// ══════════════════════════════════════════════════════════════════════════════
const storageData = ref<StorageResponse | null>(null)
const storageLoading = ref(false)
const storageError = ref('')

const scanResult = ref<CleanupScanResponse | null>(null)
const scanLoading = ref(false)
const scanDone = ref(false)
const cleanupLoading = ref(false)
const cleanupResult = ref<{ dirs: number; collections: number; reclaimed: string } | null>(null)

async function loadStorage() {
  storageLoading.value = true
  storageError.value = ''
  try {
    storageData.value = await getStorageStats()
  } catch {
    storageError.value = '获取存储统计失败'
  } finally {
    storageLoading.value = false
  }
}

async function handleScanCleanup() {
  scanLoading.value = true
  scanDone.value = false
  cleanupResult.value = null
  try {
    scanResult.value = await scanCleanup()
    scanDone.value = true
  } catch {
    storageError.value = '扫描失败'
  } finally {
    scanLoading.value = false
  }
}

async function handleExecuteCleanup() {
  if (!confirm('确认清理所有孤儿数据？此操作不可撤销。')) return
  cleanupLoading.value = true
  storageError.value = ''
  try {
    const result = await executeCleanup()
    cleanupResult.value = {
      dirs: result.cleaned_dirs,
      collections: result.cleaned_collections,
      reclaimed: result.reclaimed_human,
    }
    scanResult.value = null
    scanDone.value = false
    await loadStorage()
  } catch {
    storageError.value = '清理执行失败'
  } finally {
    cleanupLoading.value = false
  }
}

// ── Tab activation handlers ──────────────────────────────────────────────────
function switchTab(tab: typeof activeTab.value) {
  activeTab.value = tab
  if (tab === 'health' && !healthData.value) loadHealth()
  if (tab === 'tasks' && !tasksData.value) loadTasks()
  if (tab === 'storage' && !storageData.value) loadStorage()
}

onMounted(() => {
  loadConfig()
})
</script>

<template>
  <div class="system-view">

    <!-- ── Page header ─────────────────────────────────────────────────────── -->
    <div class="page-header">
      <div>
        <h1 class="page-title">系统管理</h1>
        <p class="page-desc">管理系统配置、查看运行状态与任务</p>
      </div>
    </div>

    <!-- ── Tab bar ─────────────────────────────────────────────────────────── -->
    <div class="tab-bar">
      <button
        class="tab-btn"
        :class="{ 'tab-btn--active': activeTab === 'llm' }"
        @click="switchTab('llm')"
      >
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" class="tab-icon">
          <rect x="2" y="2" width="5" height="5" rx="1"/>
          <rect x="9" y="2" width="5" height="5" rx="1"/>
          <rect x="2" y="9" width="5" height="5" rx="1"/>
          <path d="M9 11.5h5M11.5 9v5" stroke-linecap="round"/>
        </svg>
        LLM 配置
      </button>
      <button
        class="tab-btn"
        :class="{ 'tab-btn--active': activeTab === 'health' }"
        @click="switchTab('health')"
      >
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" class="tab-icon">
          <path d="M8 2a6 6 0 1 0 0 12A6 6 0 0 0 8 2z"/>
          <path d="M5 8l2 2 4-4" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        系统健康
      </button>
      <button
        class="tab-btn"
        :class="{ 'tab-btn--active': activeTab === 'tasks' }"
        @click="switchTab('tasks')"
      >
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" class="tab-icon">
          <path d="M3 4h10M3 8h7M3 12h5" stroke-linecap="round"/>
        </svg>
        任务管理
      </button>
      <button
        class="tab-btn"
        :class="{ 'tab-btn--active': activeTab === 'storage' }"
        @click="switchTab('storage')"
      >
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" class="tab-icon">
          <ellipse cx="8" cy="4.5" rx="5.5" ry="2"/>
          <path d="M2.5 4.5v3c0 1.1 2.46 2 5.5 2s5.5-.9 5.5-2v-3"/>
          <path d="M2.5 7.5v3c0 1.1 2.46 2 5.5 2s5.5-.9 5.5-2v-3"/>
        </svg>
        存储与清理
      </button>
    </div>

    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <!-- Tab 1: LLM 配置                                                        -->
    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <div v-if="activeTab === 'llm'" class="tab-content">
      <div v-if="isFirstTime" class="first-time-banner">
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" style="width:15px;height:15px;flex-shrink:0;margin-top:1px">
          <circle cx="8" cy="8" r="6.5"/>
          <path d="M8 5v4M8 11v.5" stroke-linecap="round"/>
        </svg>
        <div>
          <strong>尚未自定义配置</strong>——当前展示的是 <code>.env</code> 中的值。
          编辑并保存后，后续任务将优先使用此处的设置（无需重启服务）。
          默认供应商与模型将作为所有新任务的默认选项。
        </div>
      </div>
      <div v-if="configLoading" class="tab-loading">
        <span class="spinner" />
        <span>加载配置中...</span>
      </div>
      <template v-else>
        <div v-if="configError" class="alert alert-error" style="margin-bottom:20px">{{ configError }}</div>

        <div class="card config-card">
          <h2 class="section-title">LLM 供应商</h2>

          <div class="form-grid">
            <div class="form-group">
              <label class="form-label">默认供应商</label>
              <select v-model="llmForm.default_provider" class="form-input form-select">
                <option value="openai">OpenAI</option>
                <option value="dashscope">DashScope（阿里云）</option>
                <option value="gemini">Google Gemini</option>
                <option value="custom">自定义本地模型</option>
              </select>
            </div>
            <div class="form-group">
              <label class="form-label">默认模型名称</label>
              <input
                v-model="llmForm.default_model"
                class="form-input"
                placeholder="如 gpt-4o / qwen-plus"
              />
            </div>
          </div>

          <div class="divider" />

          <h3 class="subsection-title">OpenAI</h3>
          <div class="form-grid">
            <div class="form-group">
              <label class="form-label">API Key</label>
              <div class="input-pw-wrap">
                <input
                  v-model="llmForm.openai_api_key"
                  :type="showPasswords['openai_key'] ? 'text' : 'password'"
                  class="form-input"
                  placeholder="未配置"
                />
                <span v-if="isKeyEmpty(llmForm.openai_api_key)" class="key-empty-badge">未启用</span>
                <button type="button" class="pw-toggle-btn" @click="togglePassword('openai_key')" tabindex="-1">
                  <svg v-if="!showPasswords['openai_key']" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z"/><circle cx="8" cy="8" r="2"/></svg>
                  <svg v-else viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z"/><circle cx="8" cy="8" r="2"/><line x1="2" y1="2" x2="14" y2="14" stroke-linecap="round"/></svg>
                </button>
              </div>
            </div>
            <div class="form-group">
              <label class="form-label">Base URL</label>
              <input
                v-model="llmForm.openai_base_url"
                class="form-input"
                placeholder="https://api.openai.com/v1"
              />
            </div>
          </div>

          <div class="test-row">
            <input v-model="testModels['openai']" class="form-input test-model-input" placeholder="测试用模型名，如 gpt-4o-mini" />
            <button type="button" class="btn-test" :disabled="testResults['openai']?.loading" @click="testConnection('openai', llmForm.openai_api_key, llmForm.openai_base_url, testModels['openai'])">
              <span v-if="testResults['openai']?.loading" class="test-spinner"/>
              <span v-else>连通性测试</span>
            </button>
            <span v-if="testResults['openai']?.result" class="test-result" :class="testResults['openai'].result.success ? 'test-result--ok' : 'test-result--err'">
              <template v-if="testResults['openai'].result.success">✓ {{ testResults['openai'].result.latency_ms }}ms</template>
              <template v-else>✗ {{ testResults['openai'].result.error }}</template>
            </span>
          </div>

          <h3 class="subsection-title">DashScope（阿里云）</h3>
          <div class="form-grid">
            <div class="form-group">
              <label class="form-label">API Key</label>
              <div class="input-pw-wrap">
                <input
                  v-model="llmForm.dashscope_api_key"
                  :type="showPasswords['dashscope_key'] ? 'text' : 'password'"
                  class="form-input"
                  placeholder="未配置"
                />
                <span v-if="isKeyEmpty(llmForm.dashscope_api_key)" class="key-empty-badge">未启用</span>
                <button type="button" class="pw-toggle-btn" @click="togglePassword('dashscope_key')" tabindex="-1">
                  <svg v-if="!showPasswords['dashscope_key']" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z"/><circle cx="8" cy="8" r="2"/></svg>
                  <svg v-else viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z"/><circle cx="8" cy="8" r="2"/><line x1="2" y1="2" x2="14" y2="14" stroke-linecap="round"/></svg>
                </button>
              </div>
            </div>
          </div>

          <div class="test-row">
            <input v-model="testModels['dashscope']" class="form-input test-model-input" placeholder="测试用模型名，如 qwen-plus" />
            <button type="button" class="btn-test" :disabled="testResults['dashscope']?.loading" @click="testConnection('dashscope', llmForm.dashscope_api_key, '', testModels['dashscope'])">
              <span v-if="testResults['dashscope']?.loading" class="test-spinner"/>
              <span v-else>连通性测试</span>
            </button>
            <span v-if="testResults['dashscope']?.result" class="test-result" :class="testResults['dashscope'].result.success ? 'test-result--ok' : 'test-result--err'">
              <template v-if="testResults['dashscope'].result.success">✓ {{ testResults['dashscope'].result.latency_ms }}ms</template>
              <template v-else>✗ {{ testResults['dashscope'].result.error }}</template>
            </span>
          </div>

          <h3 class="subsection-title">Google Gemini</h3>
          <div class="form-grid">
            <div class="form-group">
              <label class="form-label">API Key</label>
              <div class="input-pw-wrap">
                <input
                  v-model="llmForm.google_api_key"
                  :type="showPasswords['google_key'] ? 'text' : 'password'"
                  class="form-input"
                  placeholder="未配置"
                />
                <span v-if="isKeyEmpty(llmForm.google_api_key)" class="key-empty-badge">未启用</span>
                <button type="button" class="pw-toggle-btn" @click="togglePassword('google_key')" tabindex="-1">
                  <svg v-if="!showPasswords['google_key']" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z"/><circle cx="8" cy="8" r="2"/></svg>
                  <svg v-else viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z"/><circle cx="8" cy="8" r="2"/><line x1="2" y1="2" x2="14" y2="14" stroke-linecap="round"/></svg>
                </button>
              </div>
            </div>
          </div>

          <div class="test-row">
            <input v-model="testModels['gemini']" class="form-input test-model-input" placeholder="测试用模型名，如 gemini-1.5-flash" />
            <button type="button" class="btn-test" :disabled="testResults['gemini']?.loading" @click="testConnection('gemini', llmForm.google_api_key, '', testModels['gemini'])">
              <span v-if="testResults['gemini']?.loading" class="test-spinner"/>
              <span v-else>连通性测试</span>
            </button>
            <span v-if="testResults['gemini']?.result" class="test-result" :class="testResults['gemini'].result.success ? 'test-result--ok' : 'test-result--err'">
              <template v-if="testResults['gemini'].result.success">✓ {{ testResults['gemini'].result.latency_ms }}ms</template>
              <template v-else>✗ {{ testResults['gemini'].result.error }}</template>
            </span>
          </div>

          <h3 class="subsection-title">自定义本地模型</h3>
          <div class="form-grid">
            <div class="form-group">
              <label class="form-label">Base URL</label>
              <input
                v-model="llmForm.custom_base_url"
                class="form-input"
                placeholder="https://your-api.example.com/v1"
              />
            </div>
            <div class="form-group">
              <label class="form-label">API Key</label>
              <div class="input-pw-wrap">
                <input
                  v-model="llmForm.custom_api_key"
                  :type="showPasswords['custom_key'] ? 'text' : 'password'"
                  class="form-input"
                  placeholder="未配置"
                />
                <span v-if="isKeyEmpty(llmForm.custom_api_key)" class="key-empty-badge">未启用</span>
                <button type="button" class="pw-toggle-btn" @click="togglePassword('custom_key')" tabindex="-1">
                  <svg v-if="!showPasswords['custom_key']" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z"/><circle cx="8" cy="8" r="2"/></svg>
                  <svg v-else viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z"/><circle cx="8" cy="8" r="2"/><line x1="2" y1="2" x2="14" y2="14" stroke-linecap="round"/></svg>
                </button>
              </div>
            </div>
          </div>

          <div class="test-row">
            <input v-model="testModels['custom']" class="form-input test-model-input" placeholder="测试用模型名（必填）" />
            <button type="button" class="btn-test" :disabled="testResults['custom']?.loading" @click="testConnection('custom', llmForm.custom_api_key, llmForm.custom_base_url, testModels['custom'])">
              <span v-if="testResults['custom']?.loading" class="test-spinner"/>
              <span v-else>连通性测试</span>
            </button>
            <span v-if="testResults['custom']?.result" class="test-result" :class="testResults['custom'].result.success ? 'test-result--ok' : 'test-result--err'">
              <template v-if="testResults['custom'].result.success">✓ {{ testResults['custom'].result.latency_ms }}ms</template>
              <template v-else>✗ {{ testResults['custom'].result.error }}</template>
            </span>
          </div>

          <div class="divider" />

          <!-- Embedding 可折叠区域 -->
          <button class="collapse-toggle" @click="showEmbedding = !showEmbedding">
            <svg
              class="collapse-chevron"
              :class="{ 'collapse-chevron--open': showEmbedding }"
              viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"
            >
              <path d="M4 6l4 4 4-4" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            Embedding 配置
            <span class="collapse-badge">{{ showEmbedding ? '收起' : '展开' }}</span>
          </button>

          <div v-if="showEmbedding" class="collapse-body">
            <div class="form-grid">
              <div class="form-group">
                <label class="form-label">Embedding API Key</label>
                <div class="input-pw-wrap">
                  <input
                    v-model="embeddingForm.api_key"
                    :type="showPasswords['emb_key'] ? 'text' : 'password'"
                    class="form-input"
                    placeholder="未配置"
                  />
                  <span v-if="isKeyEmpty(embeddingForm.api_key)" class="key-empty-badge">未启用</span>
                  <button type="button" class="pw-toggle-btn" @click="togglePassword('emb_key')" tabindex="-1">
                    <svg v-if="!showPasswords['emb_key']" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z"/><circle cx="8" cy="8" r="2"/></svg>
                    <svg v-else viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z"/><circle cx="8" cy="8" r="2"/><line x1="2" y1="2" x2="14" y2="14" stroke-linecap="round"/></svg>
                  </button>
                </div>
              </div>
              <div class="form-group">
                <label class="form-label">Embedding Base URL</label>
                <input
                  v-model="embeddingForm.base_url"
                  class="form-input"
                  placeholder="https://..."
                />
              </div>
              <div class="form-group">
                <label class="form-label">Embedding 模型</label>
                <input
                  v-model="embeddingForm.model"
                  class="form-input"
                  placeholder="text-embedding-v3"
                />
              </div>
            </div>
          </div>

          <div class="divider" />

          <h3 class="subsection-title">Wiki 生成</h3>
          <div class="form-grid">
            <div class="form-group">
              <label class="form-label">生成语言</label>
              <input
                v-model="wikiLanguage"
                class="form-input"
                placeholder="Chinese"
              />
              <p class="form-hint">示例：Chinese / English</p>
            </div>
          </div>

          <div class="save-row">
            <button
              class="btn btn-primary"
              :disabled="configSaving"
              @click="saveConfig"
            >
              <svg
                v-if="configSaving"
                class="btn-spinner"
                viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"
              >
                <path d="M13.5 2.5A7 7 0 1 0 14 8" stroke-linecap="round"/>
                <path d="M14 2.5V6h-3.5" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
              {{ configSaving ? '保存中...' : '保存配置' }}
            </button>
            <transition name="fade-msg">
              <span v-if="configSavedMsg" class="save-success-msg">
                <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" style="width:13px;height:13px;flex-shrink:0">
                  <path d="M3 8l3.5 3.5L13 4" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
                配置已保存，下次任务处理时生效
              </span>
            </transition>
          </div>
        </div>
      </template>
    </div>

    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <!-- Tab 2: 系统健康                                                        -->
    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <div v-if="activeTab === 'health'" class="tab-content">
      <div class="health-toolbar">
        <h2 class="section-title" style="margin:0">服务状态</h2>
        <button
          class="btn btn-ghost btn-sm refresh-btn"
          :disabled="healthLoading"
          @click="loadHealth"
        >
          <svg
            class="refresh-icon"
            :class="{ 'refresh-icon--spinning': healthLoading }"
            viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"
          >
            <path d="M13.5 2.5A7 7 0 1 0 14 8" stroke-linecap="round"/>
            <path d="M14 2.5V6h-3.5" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          {{ healthLoading ? '刷新中...' : '刷新' }}
        </button>
      </div>

      <div v-if="healthError" class="alert alert-error">{{ healthError }}</div>

      <div v-if="healthLoading && !healthData" class="tab-loading">
        <span class="spinner" />
        <span>检查服务状态...</span>
      </div>

      <template v-else-if="healthData">
        <!-- 服务卡片 -->
        <div class="service-grid">
          <div class="service-card card" v-for="(svc, key) in healthData.services" :key="key">
            <div class="service-card__header">
              <span
                class="status-dot"
                :class="{
                  'status-dot--ok': svc.status === 'ok',
                  'status-dot--error': svc.status === 'error',
                  'status-dot--offline': svc.status === 'offline' || svc.status === 'unknown',
                }"
              />
              <span class="service-name">{{ { database: '数据库', redis: 'Redis', chromadb: 'ChromaDB', worker: 'Celery Worker' }[key] }}</span>
            </div>
            <div class="service-status-label" :class="`service-status-label--${svc.status}`">
              {{ serviceStatusLabel(svc.status) }}
            </div>
            <div v-if="svc.latency_ms !== undefined" class="service-latency">
              延迟 {{ svc.latency_ms }} ms
            </div>
            <div v-if="svc.collection_count !== undefined" class="service-latency">
              {{ svc.collection_count }} 个集合
            </div>
          </div>
        </div>

        <!-- 统计行 -->
        <div class="stats-row">
          <div class="stat-item card">
            <div class="stat-value">{{ healthData.stats.total_repos }}</div>
            <div class="stat-label">总仓库数</div>
          </div>
          <div class="stat-item card">
            <div class="stat-value">{{ healthData.stats.total_tasks }}</div>
            <div class="stat-label">总任务数</div>
          </div>
          <div class="stat-item card">
            <div class="stat-value" :class="{ 'stat-value--active': healthData.stats.active_tasks > 0 }">
              {{ healthData.stats.active_tasks }}
            </div>
            <div class="stat-label">活跃任务</div>
          </div>
        </div>
      </template>
    </div>

    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <!-- Tab 3: 任务管理                                                        -->
    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <div v-if="activeTab === 'tasks'" class="tab-content">
      <div class="filter-bar">
        <div class="filter-group">
          <button
            v-for="f in taskStatusFilters"
            :key="f.value"
            class="filter-btn"
            :class="{ 'filter-btn--active': taskFilter === f.value }"
            @click="taskFilter = f.value"
          >{{ f.label }}</button>
        </div>
        <button
          class="btn btn-ghost btn-sm refresh-btn"
          :disabled="tasksLoading"
          @click="loadTasks"
        >
          <svg
            class="refresh-icon"
            :class="{ 'refresh-icon--spinning': tasksLoading }"
            viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"
          >
            <path d="M13.5 2.5A7 7 0 1 0 14 8" stroke-linecap="round"/>
            <path d="M14 2.5V6h-3.5" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          刷新
        </button>
      </div>

      <div v-if="tasksError" class="alert alert-error">{{ tasksError }}</div>

      <div v-if="tasksLoading && !tasksData" class="tab-loading">
        <span class="spinner" />
        <span>加载任务列表...</span>
      </div>

      <template v-else-if="tasksData">
        <div v-if="tasksData.items.length === 0" class="list-empty-inline">
          当前筛选条件下无任务记录。
        </div>
        <div v-else class="task-table-wrap card">
          <table class="task-table">
            <thead>
              <tr>
                <th>仓库</th>
                <th>任务类型</th>
                <th>状态</th>
                <th>进度</th>
                <th>创建时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="task in tasksData.items" :key="task.id">
                <td class="td-repo">{{ task.repo_name || task.repo_id }}</td>
                <td>
                  <span class="type-badge">{{ taskTypeLabel(task.type) }}</span>
                </td>
                <td>
                  <span
                    class="status-badge"
                    :class="`status-badge--${task.status}`"
                  >{{ taskStatusLabel(task.status) }}</span>
                </td>
                <td class="td-progress">
                  <div class="progress-wrap">
                    <div class="progress-bar-bg">
                      <div
                        class="progress-bar-fill"
                        :style="{ width: task.progress_pct + '%' }"
                        :class="{ 'progress-bar-fill--active': isTaskActive(task.status) }"
                      />
                    </div>
                    <span class="progress-pct">{{ task.progress_pct }}%</span>
                  </div>
                </td>
                <td class="td-date">{{ formatDate(task.created_at) }}</td>
                <td>
                  <button
                    v-if="isTaskActive(task.status)"
                    class="btn btn-sm btn-danger-ghost"
                    :disabled="cancellingTask === task.id"
                    @click="handleCancelTask(task.id)"
                  >
                    {{ cancellingTask === task.id ? '取消中...' : '取消' }}
                  </button>
                  <span v-else class="no-action">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- 分页 -->
        <div v-if="tasksData.total > taskPerPage" class="pagination">
          <button
            class="btn btn-ghost btn-sm"
            :disabled="taskPage <= 1 || tasksLoading"
            @click="taskPage--"
          >上一页</button>
          <span class="pagination-info">
            第 {{ taskPage }} 页 / 共 {{ Math.ceil(tasksData.total / taskPerPage) }} 页（{{ tasksData.total }} 条）
          </span>
          <button
            class="btn btn-ghost btn-sm"
            :disabled="taskPage >= Math.ceil(tasksData.total / taskPerPage) || tasksLoading"
            @click="taskPage++"
          >下一页</button>
        </div>
      </template>
    </div>

    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <!-- Tab 4: 存储与清理                                                      -->
    <!-- ══════════════════════════════════════════════════════════════════════ -->
    <div v-if="activeTab === 'storage'" class="tab-content">
      <div v-if="storageError" class="alert alert-error">{{ storageError }}</div>

      <!-- 存储统计 -->
      <h2 class="section-title">存储统计</h2>

      <div v-if="storageLoading && !storageData" class="tab-loading">
        <span class="spinner" />
        <span>加载存储信息...</span>
      </div>

      <div v-else-if="storageData" class="storage-grid">
        <div class="storage-card card">
          <div class="storage-card__icon">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M3 4a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4z"/>
              <path d="M3 9h14v7a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9z"/>
            </svg>
          </div>
          <div class="storage-card__body">
            <div class="storage-card__label">克隆目录</div>
            <div class="storage-card__size">{{ storageData.repos_dir.size_human }}</div>
            <div class="storage-card__meta">
              {{ storageData.repos_dir.subdirectory_count ?? '—' }} 个仓库
            </div>
            <div class="storage-card__path">{{ storageData.repos_dir.path }}</div>
          </div>
        </div>

        <div class="storage-card card">
          <div class="storage-card__icon">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5">
              <ellipse cx="10" cy="5" rx="7" ry="2.5"/>
              <path d="M3 5v4c0 1.38 3.13 2.5 7 2.5S17 10.38 17 9V5"/>
              <path d="M3 9v4c0 1.38 3.13 2.5 7 2.5S17 14.38 17 13V9"/>
            </svg>
          </div>
          <div class="storage-card__body">
            <div class="storage-card__label">ChromaDB</div>
            <div class="storage-card__size">{{ storageData.chromadb.size_human }}</div>
            <div class="storage-card__meta">向量数据库</div>
            <div class="storage-card__path">{{ storageData.chromadb.path }}</div>
          </div>
        </div>

        <div class="storage-card card">
          <div class="storage-card__icon">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M4 2h8l4 4v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z"/>
              <path d="M12 2v4h4"/>
            </svg>
          </div>
          <div class="storage-card__body">
            <div class="storage-card__label">数据库</div>
            <div class="storage-card__size">{{ storageData.database.size_human }}</div>
            <div class="storage-card__meta">SQLite 数据库</div>
            <div class="storage-card__path">{{ storageData.database.path }}</div>
          </div>
        </div>
      </div>

      <div class="divider" style="margin: 28px 0" />

      <!-- 孤儿数据清理 -->
      <div class="cleanup-section">
        <div class="cleanup-header">
          <div>
            <h2 class="section-title" style="margin-bottom:4px">孤儿数据清理</h2>
            <p class="cleanup-desc">扫描并清理已删除仓库遗留的克隆目录和 ChromaDB 集合。</p>
          </div>
          <button
            class="btn btn-secondary"
            :disabled="scanLoading"
            @click="handleScanCleanup"
          >
            <svg
              v-if="scanLoading"
              class="btn-spinner"
              viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"
            >
              <path d="M13.5 2.5A7 7 0 1 0 14 8" stroke-linecap="round"/>
              <path d="M14 2.5V6h-3.5" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <svg
              v-else
              viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8"
              style="width:14px;height:14px"
            >
              <circle cx="7" cy="7" r="4.5"/>
              <path d="M13 13l-2.5-2.5" stroke-linecap="round"/>
            </svg>
            {{ scanLoading ? '扫描中...' : '扫描孤儿数据' }}
          </button>
        </div>

        <!-- 清理执行结果 -->
        <div v-if="cleanupResult" class="alert alert-success cleanup-result">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px;flex-shrink:0">
            <path d="M3 8l3.5 3.5L13 4" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          清理完成：已清理 {{ cleanupResult.dirs }} 个目录、{{ cleanupResult.collections }} 个 ChromaDB 集合，回收空间 {{ cleanupResult.reclaimed }}。
        </div>

        <!-- 扫描结果 -->
        <template v-if="scanDone && scanResult">
          <!-- 无孤儿 -->
          <div
            v-if="scanResult.orphan_dirs.length === 0 && scanResult.orphan_chromadb_collections.length === 0"
            class="alert alert-success cleanup-result"
          >
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px;flex-shrink:0">
              <path d="M3 8l3.5 3.5L13 4" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            无孤儿数据，无需清理。
          </div>

          <!-- 有孤儿 -->
          <div v-else class="orphan-panel card">
            <div class="orphan-summary">
              发现孤儿数据，可回收
              <strong class="orphan-size">{{ scanResult.total_reclaimable_human }}</strong>
            </div>

            <div v-if="scanResult.orphan_dirs.length > 0" class="orphan-list-section">
              <div class="orphan-list-title">孤儿目录（{{ scanResult.orphan_dirs.length }} 个）</div>
              <div class="orphan-list">
                <div
                  v-for="dir in scanResult.orphan_dirs"
                  :key="dir.path"
                  class="orphan-item"
                >
                  <code class="orphan-path">{{ dir.path }}</code>
                  <span class="orphan-item-size">{{ dir.size_human }}</span>
                </div>
              </div>
            </div>

            <div v-if="scanResult.orphan_chromadb_collections.length > 0" class="orphan-list-section">
              <div class="orphan-list-title">孤儿 ChromaDB 集合（{{ scanResult.orphan_chromadb_collections.length }} 个）</div>
              <div class="orphan-list">
                <div
                  v-for="col in scanResult.orphan_chromadb_collections"
                  :key="col"
                  class="orphan-item"
                >
                  <code class="orphan-path">{{ col }}</code>
                </div>
              </div>
            </div>

            <div class="orphan-actions">
              <button
                class="btn btn-danger"
                :disabled="cleanupLoading"
                @click="handleExecuteCleanup"
              >
                <svg
                  v-if="cleanupLoading"
                  class="btn-spinner"
                  viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"
                >
                  <path d="M13.5 2.5A7 7 0 1 0 14 8" stroke-linecap="round"/>
                  <path d="M14 2.5V6h-3.5" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
                {{ cleanupLoading ? '清理中...' : '执行清理' }}
              </button>
            </div>
          </div>
        </template>
      </div>
    </div>

  </div>
</template>

<style scoped>
.system-view {
  max-width: 1100px;
  margin: 0 auto;
  padding: 36px 20px 80px;
  width: 100%;
}

/* ── Page header ──────────────────────────────────────────────────────────── */
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 28px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--border-color);
}

.page-title {
  font-size: var(--font-size-2xl);
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 4px;
  letter-spacing: -0.02em;
}

.page-desc {
  color: var(--text-tertiary);
  font-size: var(--font-size-sm);
}

/* ── Tab bar ──────────────────────────────────────────────────────────────── */
.tab-bar {
  display: flex;
  gap: 0;
  border-bottom: 1px solid var(--border-color);
  margin-bottom: 28px;
  overflow-x: auto;
}

.tab-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 10px 20px;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  font-size: var(--font-size-sm);
  font-weight: 500;
  color: var(--text-muted);
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s;
  white-space: nowrap;
}

.tab-btn:hover {
  color: var(--text-secondary);
}

.tab-btn--active {
  color: var(--color-primary);
  border-bottom-color: var(--color-primary);
}

.tab-icon {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
}

/* ── Tab content ──────────────────────────────────────────────────────────── */
.tab-content {
  animation: tab-in 0.15s ease;
}

@keyframes tab-in {
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: translateY(0); }
}

.tab-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 48px 20px;
  color: var(--text-muted);
  font-size: var(--font-size-sm);
}

/* ── Section titles ───────────────────────────────────────────────────────── */
.section-title {
  font-size: var(--font-size-lg);
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 16px;
}

.subsection-title {
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 12px;
  margin-top: 4px;
}

.divider {
  border: none;
  border-top: 1px solid var(--border-color);
  margin: 20px 0;
}

/* ── Config card ──────────────────────────────────────────────────────────── */
.config-card {
  max-width: 760px;
}

.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 4px;
}

@media (max-width: 600px) {
  .form-grid { grid-template-columns: 1fr; }
}

/* ── Collapse / Embedding ─────────────────────────────────────────────────── */
.collapse-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: none;
  border: none;
  font-size: var(--font-size-sm);
  font-weight: 500;
  color: var(--text-secondary);
  cursor: pointer;
  padding: 6px 0;
  transition: color 0.15s;
}
.collapse-toggle:hover { color: var(--text-primary); }

.collapse-chevron {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
  transition: transform 0.2s;
}
.collapse-chevron--open { transform: rotate(180deg); }

.collapse-badge {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
  margin-left: 2px;
}

.collapse-body {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border-color);
}

/* ── Save row ─────────────────────────────────────────────────────────────── */
.save-row {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: 8px;
  padding-top: 20px;
  border-top: 1px solid var(--border-color);
}

.save-success-msg {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--font-size-sm);
  color: var(--color-success);
  font-weight: 500;
}

.fade-msg-enter-active, .fade-msg-leave-active {
  transition: opacity 0.3s;
}
.fade-msg-enter-from, .fade-msg-leave-to {
  opacity: 0;
}

/* ── Btn spinner ──────────────────────────────────────────────────────────── */
.btn-spinner {
  width: 13px;
  height: 13px;
  animation: spin 0.8s linear infinite;
  flex-shrink: 0;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}

/* ── Health tab ───────────────────────────────────────────────────────────── */
.health-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.service-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
  margin-bottom: 20px;
}

@media (max-width: 800px) {
  .service-grid { grid-template-columns: repeat(2, 1fr); }
}

@media (max-width: 480px) {
  .service-grid { grid-template-columns: 1fr; }
}

.service-card {
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.service-card__header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.status-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  flex-shrink: 0;
}
.status-dot--ok { background: var(--color-success); box-shadow: 0 0 6px rgba(16, 185, 129, 0.4); }
.status-dot--error { background: var(--color-error); box-shadow: 0 0 6px rgba(239, 68, 68, 0.4); }
.status-dot--offline { background: var(--text-muted); }

.service-name {
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--text-primary);
}

.service-status-label {
  font-size: var(--font-size-xs);
  font-weight: 500;
}
.service-status-label--ok { color: var(--color-success); }
.service-status-label--error { color: var(--color-error); }
.service-status-label--offline,
.service-status-label--unknown { color: var(--text-muted); }

.service-latency {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
}

.stats-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
  margin-top: 8px;
}

@media (max-width: 480px) {
  .stats-row { grid-template-columns: 1fr; }
}

.stat-item {
  padding: 18px 20px;
  text-align: center;
}

.stat-value {
  font-size: var(--font-size-2xl);
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.2;
  margin-bottom: 4px;
}

.stat-value--active {
  color: var(--color-primary);
}

.stat-label {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
  font-weight: 500;
}

/* ── Tasks tab ────────────────────────────────────────────────────────────── */
.filter-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  flex-wrap: wrap;
  gap: 8px;
}

.filter-group { display: flex; gap: 4px; flex-wrap: wrap; }

.filter-btn {
  padding: 5px 14px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-full);
  background: var(--bg-primary);
  font-size: var(--font-size-xs);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s;
  font-weight: 500;
}
.filter-btn:hover { background: var(--bg-hover); border-color: var(--border-color-strong); }
.filter-btn--active {
  background: var(--color-primary);
  color: white;
  border-color: var(--color-primary);
}

.refresh-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.refresh-icon {
  width: 13px;
  height: 13px;
  flex-shrink: 0;
  transition: transform 0.6s;
}
.refresh-icon--spinning {
  animation: spin 0.8s linear infinite;
}

.list-empty-inline {
  padding: 40px 20px;
  text-align: center;
  color: var(--text-muted);
  font-size: var(--font-size-sm);
}

.task-table-wrap {
  padding: 0;
  overflow: hidden;
  overflow-x: auto;
}

.task-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--font-size-sm);
}

.task-table th {
  padding: 10px 14px;
  text-align: left;
  font-size: var(--font-size-xs);
  font-weight: 600;
  color: var(--text-muted);
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border-color);
  white-space: nowrap;
}

.task-table td {
  padding: 10px 14px;
  border-bottom: 1px solid var(--border-color);
  vertical-align: middle;
  color: var(--text-primary);
}

.task-table tbody tr:last-child td {
  border-bottom: none;
}

.task-table tbody tr:hover td {
  background: var(--bg-secondary);
}

.td-repo {
  font-weight: 500;
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.td-date {
  color: var(--text-muted);
  font-size: var(--font-size-xs);
  white-space: nowrap;
}

.type-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  font-size: var(--font-size-xs);
  font-weight: 500;
  white-space: nowrap;
}

/* Status badges */
.status-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  font-size: var(--font-size-xs);
  font-weight: 500;
  white-space: nowrap;
}
.status-badge--completed { background: #f0fdf4; color: #15803d; }
.status-badge--failed    { background: #fef2f2; color: #b91c1c; }
.status-badge--cancelled { background: var(--bg-tertiary); color: var(--text-muted); }
.status-badge--interrupted { background: #fffbeb; color: #92400e; }
.status-badge--pending,
.status-badge--running,
.status-badge--cloning,
.status-badge--parsing,
.status-badge--embedding,
.status-badge--generating,
.status-badge--syncing {
  background: #eff6ff;
  color: #1d4ed8;
}

/* Progress */
.td-progress { min-width: 120px; }
.progress-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
}
.progress-bar-bg {
  flex: 1;
  height: 5px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-full);
  overflow: hidden;
}
.progress-bar-fill {
  height: 100%;
  background: var(--border-color-strong);
  border-radius: var(--radius-full);
  transition: width 0.3s;
}
.progress-bar-fill--active {
  background: var(--color-primary);
}
.progress-pct {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
  white-space: nowrap;
  min-width: 30px;
  text-align: right;
}

.no-action {
  color: var(--text-muted);
  font-size: var(--font-size-xs);
}

.btn-danger-ghost {
  color: #ef4444;
  border: 1px solid #fca5a5;
  background: transparent;
}
.btn-danger-ghost:hover:not(:disabled) {
  background: #fef2f2;
  color: #dc2626;
}
.btn-danger-ghost:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Pagination */
.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  margin-top: 20px;
}

.pagination-info {
  font-size: var(--font-size-sm);
  color: var(--text-muted);
}

/* ── Storage tab ──────────────────────────────────────────────────────────── */
.storage-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
  margin-bottom: 8px;
}

@media (max-width: 768px) {
  .storage-grid { grid-template-columns: 1fr; }
}

.storage-card {
  display: flex;
  gap: 14px;
  align-items: flex-start;
  padding: 18px;
}

.storage-card__icon {
  width: 36px;
  height: 36px;
  background: var(--bg-tertiary);
  border-radius: var(--radius);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  color: var(--text-muted);
}

.storage-card__icon svg {
  width: 18px;
  height: 18px;
}

.storage-card__body {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.storage-card__label {
  font-size: var(--font-size-xs);
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.storage-card__size {
  font-size: var(--font-size-xl);
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.2;
}

.storage-card__meta {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
}

.storage-card__path {
  font-size: 11px;
  color: var(--text-muted);
  font-family: var(--font-mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-top: 2px;
}

/* ── Cleanup section ──────────────────────────────────────────────────────── */
.cleanup-section { }

.cleanup-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.cleanup-desc {
  font-size: var(--font-size-sm);
  color: var(--text-muted);
}

.cleanup-result {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}

.orphan-panel {
  margin-top: 4px;
}

.orphan-summary {
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  margin-bottom: 16px;
}

.orphan-size {
  color: var(--color-error);
  font-size: var(--font-size-md);
}

.orphan-list-section {
  margin-bottom: 16px;
}

.orphan-list-title {
  font-size: var(--font-size-xs);
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 8px;
}

.orphan-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 200px;
  overflow-y: auto;
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  padding: 8px 10px;
}

.orphan-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 3px 0;
}

.orphan-path {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.orphan-item-size {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
  white-space: nowrap;
  flex-shrink: 0;
}

.orphan-actions {
  padding-top: 16px;
  border-top: 1px solid var(--border-color);
  display: flex;
  justify-content: flex-end;
}

/* ── First-time banner ────────────────────────────────────────────────────── */
.first-time-banner {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 16px;
  background: #eff6ff;
  border: 1px solid #93c5fd;
  border-radius: var(--radius);
  color: #1d4ed8;
  font-size: var(--font-size-sm);
  line-height: 1.6;
  margin-bottom: 20px;
}
.first-time-banner strong { font-weight: 600; }
.first-time-banner code {
  font-family: var(--font-mono);
  font-size: var(--font-size-xs);
  background: rgba(37, 99, 235, 0.12);
  padding: 1px 5px;
  border-radius: 3px;
}
[data-theme="dark"] .first-time-banner {
  background: rgba(37, 99, 235, 0.1);
  border-color: rgba(37, 99, 235, 0.3);
  color: #93c5fd;
}

/* ── Password input group ─────────────────────────────────────────────────── */
.input-pw-wrap {
  position: relative;
  display: flex;
  align-items: center;
}
.input-pw-wrap .form-input {
  padding-right: 64px;
}
.key-empty-badge {
  position: absolute;
  right: 36px;
  font-size: 10px;
  font-weight: 600;
  color: var(--text-muted);
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  padding: 1px 6px;
  pointer-events: none;
  white-space: nowrap;
}
.pw-toggle-btn {
  position: absolute;
  right: 0;
  top: 0;
  bottom: 0;
  width: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  border-radius: 0 var(--radius) var(--radius) 0;
  transition: color 0.15s;
}
.pw-toggle-btn:hover { color: var(--text-secondary); }
.pw-toggle-btn svg { width: 14px; height: 14px; flex-shrink: 0; }

/* ── Connection test row ──────────────────────────────────────────────────── */
.test-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 4px;
  margin-bottom: 8px;
}
.test-model-input {
  flex: 1;
  max-width: 260px;
  padding: 5px 10px;
  font-size: 13px;
}

.btn-test {
  padding: 5px 14px;
  font-size: var(--font-size-xs);
  font-weight: 500;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-full);
  background: var(--bg-primary);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 28px;
}

.btn-test:hover:not(:disabled) {
  background: var(--bg-hover);
  border-color: var(--border-color-strong);
}

.btn-test:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.test-spinner {
  display: inline-block;
  width: 10px;
  height: 10px;
  border: 1.5px solid var(--border-color-strong);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}

.test-result {
  font-size: var(--font-size-xs);
  font-weight: 500;
  max-width: 360px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.test-result--ok { color: var(--color-success, #16a34a); }
.test-result--err { color: var(--color-error, #dc2626); }
</style>
