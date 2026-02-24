import { ref } from 'vue'
import mermaid from 'mermaid'

let mermaidInitialized = false

/**
 * 将 graph/flowchart 图表中的中文节点ID替换为安全的ASCII ID。
 * Mermaid 10.x 要求节点ID只能含 ASCII 字母/数字/下划线，中文字符作为节点ID会导致
 * "Syntax error in text" 错误。中文文本只能放在标签括号内，如 A[中文标签]。
 */
function sanitizeMermaidGraph(code: string): string {
  if (!/^(?:graph|flowchart)\s+/i.test(code.trim())) return code
  if (!/[\u4e00-\u9fff]/.test(code)) return code

  const CHINESE = /[\u4e00-\u9fff]/
  const idMap = new Map<string, string>()
  let counter = 0
  const lines = code.split('\n')

  // 第一遍：收集所有中文节点ID（去除标签内容后剩余的中文token）
  for (let i = 1; i < lines.length; i++) {
    const t = lines[i].trim()
    if (!t || /^(?:subgraph|end|style|classDef|class\s|%%)/i.test(t)) continue
    // 按箭头分割，逐个检查节点引用
    const parts = t.split(/\s*(?:-->|---|\|[^|]*\||==>|-\.->)\s*/)
    for (const part of parts) {
      // 去除标签括号内容后取节点ID
      const nodeId = part.trim().replace(/\s*[\[({].*/, '').trim()
      if (nodeId && CHINESE.test(nodeId) && !idMap.has(nodeId)) {
        idMap.set(nodeId, `N${counter++}`)
      }
    }
  }

  if (idMap.size === 0) return code

  // 按长度降序排序，防止短ID误匹配长ID前缀
  const sortedIds = [...idMap.keys()].sort((a, b) => b.length - a.length)
  const ARROW_SPLIT = /(\s*(?:-->|---|\|[^|]*\||==>|-\.->)\s*)/

  // 第二遍：逐行替换
  return lines.map((line, i) => {
    if (i === 0 || !CHINESE.test(line)) return line
    const t = line.trim()
    if (/^(?:subgraph|end|style|classDef|class\s|%%)/i.test(t)) return line

    const leadWS = line.match(/^(\s*)/)?.[1] ?? ''
    // 按箭头分割（保留分隔符）
    const parts = t.split(ARROW_SPLIT)
    const replaced = parts.map((part, idx) => {
      if (idx % 2 !== 0) return part // 奇数索引是箭头符号，直接保留
      for (const chId of sortedIds) {
        if (part.trimStart().startsWith(chId)) {
          const safeId = idMap.get(chId)!
          const lead = part.match(/^(\s*)/)?.[1] ?? ''
          const rest = part.trimStart().slice(chId.length)
          // 后跟标签括号：只替换ID，保留标签内容
          if (/^\s*[\[({]/.test(rest)) return lead + safeId + rest
          // 裸节点：替换ID并补充默认标签
          return lead + `${safeId}["${chId}"]` + rest
        }
      }
      return part
    })
    return leadWS + replaced.join('')
  }).join('\n')
}

/**
 * 将 erDiagram 中的中文关系标签用双引号包裹。
 * Mermaid 10.x 的 erDiagram 关系标签（冒号后的文字）必须是 ASCII 或带双引号的字符串。
 * 示例：`Project ||--o{ Wiki : 拥有` → `Project ||--o{ Wiki : "拥有"`
 */
function sanitizeMermaidEr(code: string): string {
  if (!/^erDiagram/i.test(code.trim())) return code
  if (!/[\u4e00-\u9fff]/.test(code)) return code
  // 匹配行尾的 `: label`，若 label 含中文且未被引号包裹则加引号
  return code.replace(/:\s*([^\n"]+?)(\s*)$/gm, (_match, label, trailing) => {
    const trimmed = label.trim()
    if (/[\u4e00-\u9fff]/.test(trimmed)) {
      return `: "${trimmed}"${trailing}`
    }
    return _match
  })
}

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
    const sanitized = sanitizeMermaidEr(sanitizeMermaidGraph(code))
    try {
      const { svg } = await mermaid.render(`mermaid-svg-${id}`, sanitized)
      return { svg, error: null }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      return { svg: '', error: msg }
    }
  }

  return { initMermaid, renderDiagram, isInitialized }
}
