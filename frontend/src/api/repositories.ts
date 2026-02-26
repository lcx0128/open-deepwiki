import apiClient from './client'

export interface SubmitRepoRequest {
  url: string
  pat_token?: string
  branch?: string
  llm_provider?: string
  llm_model?: string
}

export interface SubmitRepoResponse {
  task_id: string
  repo_id: string
  status: string
  message: string
}

export interface RepositoryItem {
  id: string
  url: string
  name: string
  platform: string
  status: 'pending' | 'cloning' | 'ready' | 'error' | 'syncing'
  last_synced_at: string | null
  created_at: string
}

export interface RepositoryListResponse {
  items: RepositoryItem[]
  total: number
  page: number
  per_page: number
}

export interface TaskStatusResponse {
  id: string
  repo_id: string
  type: string
  status: 'pending' | 'cloning' | 'parsing' | 'embedding' | 'generating' | 'completed' | 'failed' | 'cancelled'
  progress_pct: number
  current_stage: string | null
  files_total: number
  files_processed: number
  error_msg: string | null
  failed_at_stage: string | null
  created_at: string
  updated_at: string
}

export async function submitRepository(data: SubmitRepoRequest): Promise<SubmitRepoResponse> {
  const response = await apiClient.post<SubmitRepoResponse>('/repositories', data)
  return response.data
}

export async function getRepositories(
  page = 1,
  perPage = 20,
  status?: string
): Promise<RepositoryListResponse> {
  const params: Record<string, unknown> = { page, per_page: perPage }
  if (status) params.status = status
  const response = await apiClient.get<RepositoryListResponse>('/repositories', { params })
  return response.data
}

export async function deleteRepository(repoId: string): Promise<void> {
  await apiClient.delete(`/repositories/${repoId}`)
}

export async function reprocessRepository(
  repoId: string,
  data?: { llm_provider?: string; llm_model?: string }
): Promise<SubmitRepoResponse> {
  const response = await apiClient.post<SubmitRepoResponse>(
    `/repositories/${repoId}/reprocess`,
    data || {}
  )
  return response.data
}

export async function getTaskStatus(taskId: string): Promise<TaskStatusResponse> {
  const response = await apiClient.get<TaskStatusResponse>(`/tasks/${taskId}`)
  return response.data
}

export interface FileContentResponse {
  file_path: string
  content: string
  start_line: number
  total_lines: number
  language: string
}

export async function syncRepository(
  repoId: string,
  data?: { llm_provider?: string; llm_model?: string }
): Promise<SubmitRepoResponse> {
  const response = await apiClient.post<SubmitRepoResponse>(
    `/repositories/${repoId}/sync`,
    data || {}
  )
  return response.data
}

export async function getFileContent(
  repoId: string,
  filePath: string,
  startLine?: number,
  endLine?: number
): Promise<FileContentResponse> {
  const params: Record<string, unknown> = { path: filePath }
  if (startLine !== undefined) params.start_line = startLine
  if (endLine !== undefined) params.end_line = endLine
  const response = await apiClient.get<FileContentResponse>(
    `/repositories/${repoId}/file`,
    { params }
  )
  return response.data
}
