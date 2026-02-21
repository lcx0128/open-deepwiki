import apiClient from './client'

export interface WikiPage {
  id: string
  title: string
  importance: 'high' | 'medium' | 'low'
  content_md: string
  relevant_files: string[]
  order_index: number
}

export interface WikiSection {
  id: string
  title: string
  order_index: number
  pages: WikiPage[]
}

export interface WikiResponse {
  id: string
  repo_id: string
  title: string
  llm_provider: string
  llm_model: string
  created_at: string
  sections: WikiSection[]
}

export interface RegenerateWikiRequest {
  llm_provider?: string
  llm_model?: string
  pages?: string[]
}

export interface RegenerateWikiResponse {
  task_id: string
  message: string
}

export async function getWiki(repoId: string): Promise<WikiResponse> {
  const response = await apiClient.get<WikiResponse>(`/wiki/${repoId}`)
  return response.data
}

export async function regenerateWiki(
  repoId: string,
  data?: RegenerateWikiRequest
): Promise<RegenerateWikiResponse> {
  const response = await apiClient.post<RegenerateWikiResponse>(
    `/wiki/${repoId}/regenerate`,
    data || {}
  )
  return response.data
}

export async function deleteWiki(repoId: string): Promise<void> {
  await apiClient.delete(`/wiki/${repoId}`)
}

export async function getWikiPage(repoId: string, pageId: string): Promise<WikiPage> {
  const response = await apiClient.get<WikiPage>(`/wiki/${repoId}/pages/${pageId}`)
  return response.data
}
