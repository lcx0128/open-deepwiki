// 对话 API - 对应模块五（RAG + 对话）
// 后端端点: GET /api/chat/stream?repo_id=&session_id=&query=

export interface ChunkRef {
  file_path: string
  start_line: number
  end_line: number
  name: string
}

export interface SessionMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  chunk_refs?: ChunkRef[]
  timestamp: string
}

export async function getChatSession(sessionId: string): Promise<{ session_id: string; messages: SessionMessage[] }> {
  const baseUrl = import.meta.env.VITE_API_BASE_URL || '/api'
  const response = await fetch(`${baseUrl}/chat/sessions/${sessionId}`)
  if (!response.ok) throw new Error(`Session not found: ${response.status}`)
  return response.json()
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

export interface DeepResearchStreamOptions {
  repoId: string
  sessionId?: string
  query: string
  messages: Array<{ role: string; content: string }>
  onSessionId: (sessionId: string) => void
  onToken: (token: string) => void
  onChunkRefs: (refs: ChunkRef[]) => void
  onDeepResearchContinue: (iteration: number) => void
  onDone: () => void
  onError: (message: string) => void
}

export function createDeepResearchStream(options: DeepResearchStreamOptions): AbortController {
  const ac = new AbortController()
  const baseUrl = import.meta.env.VITE_API_BASE_URL || '/api'

  fetch(`${baseUrl}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      repo_id: options.repoId,
      session_id: options.sessionId || null,
      query: options.query,
      deep_research: true,
      messages: options.messages,
    }),
    signal: ac.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        options.onError(`HTTP ${response.status}: ${response.statusText}`)
        return
      }
      const reader = response.body!.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          try {
            const data = JSON.parse(line.slice(6))
            switch (data.type) {
              case 'session_id':
                options.onSessionId(data.session_id)
                break
              case 'token':
                options.onToken(data.content)
                break
              case 'chunk_refs':
                options.onChunkRefs(data.refs || [])
                break
              case 'deep_research_continue':
                options.onDeepResearchContinue(data.iteration)
                break
              case 'done':
                options.onDone()
                return
              case 'error':
                options.onError(data.error || '未知错误')
                return
            }
          } catch {
            // 忽略非 JSON 数据
          }
        }
      }
    })
    .catch((e: Error) => {
      if (e.name !== 'AbortError') {
        options.onError(e.message || 'SSE 连接失败')
      }
    })

  return ac
}
