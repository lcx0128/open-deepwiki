// 对话 API - 对应模块五（RAG + 对话）
// 后端端点: GET /api/chat/stream?repo_id=&session_id=&query=

export interface ChunkRef {
  file_path: string
  start_line: number
  end_line: number
  name: string
}

export interface ChatStreamOptions {
  repoId: string
  sessionId?: string
  query: string
  onSessionId: (sessionId: string) => void
  onToken: (token: string) => void
  onChunkRefs: (refs: ChunkRef[]) => void
  onDone: () => void
  onError: (message: string) => void
}

export function createChatStream(options: ChatStreamOptions): EventSource {
  const baseUrl = import.meta.env.VITE_API_BASE_URL || '/api'
  const params = new URLSearchParams({
    repo_id: options.repoId,
    session_id: options.sessionId || '',
    query: options.query,
  })
  const url = `${baseUrl}/chat/stream?${params.toString()}`

  const eventSource = new EventSource(url)

  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      switch (data.type) {
        case 'session_id':
          options.onSessionId(data.session_id)
          break
        case 'token':
          options.onToken(data.content)
          break
        case 'chunk_refs':
          options.onChunkRefs(data.refs)
          break
        case 'done':
          eventSource.close()
          options.onDone()
          break
        case 'error':
          eventSource.close()
          options.onError(data.error || '未知错误')
          break
      }
    } catch {
      // 忽略非 JSON 消息（心跳等）
    }
  }

  eventSource.onerror = () => {
    eventSource.close()
    options.onError('SSE 连接中断')
  }

  return eventSource
}
