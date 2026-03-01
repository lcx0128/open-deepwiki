import apiClient from './client'

// ── Types ──────────────────────────────────────────────────────────────────

export interface ServiceStatus {
  status: 'ok' | 'error' | 'offline' | 'unknown'
  latency_ms?: number
  collection_count?: number
}

export interface HealthResponse {
  status: 'healthy' | 'degraded'
  services: {
    database: ServiceStatus
    redis: ServiceStatus
    chromadb: ServiceStatus
    worker: ServiceStatus
  }
  stats: {
    total_repos: number
    total_tasks: number
    active_tasks: number
  }
}

export interface LlmConfig {
  default_provider: string
  default_model: string
  openai_api_key: string
  openai_base_url: string
  dashscope_api_key: string
  google_api_key: string
  custom_base_url: string
  custom_api_key: string
}

export interface EmbeddingConfig {
  api_key: string
  base_url: string
  model: string
}

export interface SystemConfig {
  is_customized?: boolean
  llm: LlmConfig
  embedding: EmbeddingConfig
  wiki_language: string
}

export interface TaskItem {
  id: string
  repo_id: string
  repo_name: string
  type: string
  status: string
  progress_pct: number
  created_at: string
  error_msg: string | null
  failed_at_stage: string | null
}

export interface TaskListResponse {
  items: TaskItem[]
  total: number
  page: number
  per_page: number
}

export interface StorageDirInfo {
  path: string
  exists: boolean
  size_bytes: number
  size_human: string
  subdirectory_count?: number
}

export interface StorageResponse {
  repos_dir: StorageDirInfo
  chromadb: StorageDirInfo
  database: {
    path: string
    size_bytes: number
    size_human: string
  }
}

export interface OrphanDir {
  path: string
  size_bytes: number
  size_human: string
}

export interface CleanupScanResponse {
  orphan_dirs: OrphanDir[]
  orphan_chromadb_collections: string[]
  total_reclaimable_bytes: number
  total_reclaimable_human: string
}

export interface CleanupExecuteResponse {
  cleaned_dirs: number
  cleaned_collections: number
  reclaimed_bytes: number
  reclaimed_human: string
}

// ── API Functions ──────────────────────────────────────────────────────────

export async function getSystemConfig(): Promise<SystemConfig> {
  const response = await apiClient.get<SystemConfig>('/system/config')
  return response.data
}

export async function updateSystemConfig(config: Partial<SystemConfig>): Promise<SystemConfig> {
  const response = await apiClient.put<SystemConfig>('/system/config', config)
  return response.data
}

export async function getSystemHealth(): Promise<HealthResponse> {
  const response = await apiClient.get<HealthResponse>('/system/health')
  return response.data
}

export async function getSystemTasks(
  page = 1,
  perPage = 20,
  status?: string
): Promise<TaskListResponse> {
  const params: Record<string, unknown> = { page, per_page: perPage }
  if (status) params.status = status
  const response = await apiClient.get<TaskListResponse>('/system/tasks', { params })
  return response.data
}

export async function cancelTask(taskId: string): Promise<void> {
  await apiClient.post(`/system/tasks/${taskId}/cancel`)
}

export async function getStorageStats(): Promise<StorageResponse> {
  const response = await apiClient.get<StorageResponse>('/system/storage')
  return response.data
}

export async function scanCleanup(): Promise<CleanupScanResponse> {
  const response = await apiClient.post<CleanupScanResponse>('/system/cleanup/scan')
  return response.data
}

export async function executeCleanup(): Promise<CleanupExecuteResponse> {
  const response = await apiClient.post<CleanupExecuteResponse>('/system/cleanup/execute')
  return response.data
}

export interface TestConnectionRequest {
  provider: string
  api_key?: string
  base_url?: string
  model?: string
}

export interface TestConnectionResponse {
  success: boolean
  latency_ms: number | null
  error: string | null
}

export async function testLlmConnection(req: TestConnectionRequest): Promise<TestConnectionResponse> {
  const response = await apiClient.post<TestConnectionResponse>('/system/config/test', req)
  return response.data
}
