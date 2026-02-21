import { ref } from 'vue'
import mermaid from 'mermaid'

let mermaidInitialized = false

export function useMermaid() {
  const isInitialized = ref(false)

  function initMermaid(isDark = false) {
    if (mermaidInitialized) return
    mermaid.initialize({
      startOnLoad: false,
      theme: isDark ? 'dark' : 'default',
      securityLevel: 'loose',
      flowchart: { useMaxWidth: true, htmlLabels: true },
      sequence: { useMaxWidth: true },
      gantt: { useMaxWidth: true },
    })
    mermaidInitialized = true
    isInitialized.value = true
  }

  async function renderDiagram(
    id: string,
    code: string
  ): Promise<{ svg: string; error: string | null }> {
    initMermaid()
    try {
      const { svg } = await mermaid.render(`mermaid-svg-${id}`, code)
      return { svg, error: null }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      return { svg: '', error: msg }
    }
  }

  return { initMermaid, renderDiagram, isInitialized }
}
