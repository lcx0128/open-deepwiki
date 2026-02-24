<script setup lang="ts">
import { computed, watch } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import hljs from 'highlight.js'
import MermaidBlock from './MermaidBlock.vue'

function handleCopyClick(e: MouseEvent) {
  const btn = (e.target as HTMLElement).closest<HTMLButtonElement>('.code-block__copy')
  if (!btn) return
  const code = btn.closest('.code-block')?.querySelector('code')
  if (!code) return
  const text = code.textContent || ''
  navigator.clipboard?.writeText(text).then(
    () => { btn.textContent = '已复制'; setTimeout(() => { if (btn) btn.textContent = '复制' }, 1500) },
    () => { btn.textContent = '失败'; setTimeout(() => { if (btn) btn.textContent = '复制' }, 1500) }
  )
}

// marked v9 calls renderer.code(code: string, lang?: string) — positional args, not token object
marked.use({
  breaks: true,
  gfm: true,
  renderer: {
    code(code: string, lang?: string): string {
      const safeText = code ?? ''
      const language = lang && hljs.getLanguage(lang) ? lang : 'plaintext'
      let highlighted: string
      try {
        highlighted = language !== 'plaintext'
          ? hljs.highlight(safeText, { language }).value
          : hljs.highlightAuto(safeText).value
      } catch {
        highlighted = safeText.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      }
      const langLabel = language !== 'plaintext' ? language : ''
      return `<div class="code-block">
    <div class="code-block__header">
      <span class="code-block__lang">${langLabel}</span>
      <button class="code-block__copy">复制</button>
    </div>
    <pre><code class="hljs language-${language}">${highlighted}</code></pre>
  </div>`
    }
  }
})

const props = defineProps<{ content: string; compactCode?: boolean }>()

const emit = defineEmits<{
  'code-block-click': [blockId: string]
  'code-blocks-extracted': [blocks: Array<{id: string, lang: string, content: string}>]
}>()

interface Block {
  type: 'html' | 'mermaid' | 'code'
  content: string
  id: string
  lang?: string
}

const parsedBlocks = computed<Block[]>(() => {
  const blocks: Block[] = []
  let counter = 0

  if (!props.content) return blocks

  // Split out ```mermaid blocks first
  const parts = props.content.split(/(```mermaid\s*\n[\s\S]*?\n\s*```)/g)

  for (const part of parts) {
    const mermaidMatch = part.match(/^```mermaid\s*\n([\s\S]*?)\n\s*```$/)
    if (mermaidMatch) {
      blocks.push({
        type: 'mermaid',
        content: mermaidMatch[1].trim(),
        id: `md-mermaid-${counter++}`,
      })
    } else if (props.compactCode) {
      // Further split out large code blocks
      const codeParts = part.split(/(```\w*\s*\n[\s\S]*?\n\s*```)/g)
      for (const codePart of codeParts) {
        const codeMatch = codePart.match(/^```(\w*)\s*\n([\s\S]*?)\n\s*```$/)
        if (codeMatch) {
          const lang = codeMatch[1]
          const code = codeMatch[2]
          const lineCount = code.trim().split('\n').length
          if (lineCount > 6) {
            // Large code block → extract to right panel
            blocks.push({
              type: 'code',
              content: code.trim(),
              lang,
              id: `md-code-${counter++}`,
            })
          } else {
            // Small code block → render inline
            if (codePart.trim()) {
              const html = DOMPurify.sanitize(
                marked.parse(codePart) as string,
                { FORCE_BODY: true }
              )
              blocks.push({ type: 'html', content: html, id: `md-html-${counter++}` })
            }
          }
        } else if (codePart.trim()) {
          const html = DOMPurify.sanitize(
            marked.parse(codePart) as string,
            { FORCE_BODY: true }
          )
          blocks.push({ type: 'html', content: html, id: `md-html-${counter++}` })
        }
      }
    } else if (part.trim()) {
      const html = DOMPurify.sanitize(
        marked.parse(part) as string,
        { FORCE_BODY: true }
      )
      blocks.push({
        type: 'html',
        content: html,
        id: `md-html-${counter++}`,
      })
    }
  }

  return blocks
})

watch(parsedBlocks, (blocks) => {
  if (!props.compactCode) return
  const codeBlocks = blocks
    .filter(b => b.type === 'code')
    .map(b => ({ id: b.id, lang: b.lang || '', content: b.content }))
  emit('code-blocks-extracted', codeBlocks)
}, { immediate: true })
</script>

<template>
  <div class="markdown-view" @click="handleCopyClick">
    <template v-for="block in parsedBlocks" :key="block.id">
      <div
        v-if="block.type === 'html'"
        class="markdown-body"
        v-html="block.content"
      />
      <MermaidBlock
        v-else-if="block.type === 'mermaid'"
        :code="block.content"
        :id="block.id"
      />
      <div
        v-else-if="block.type === 'code'"
        class="code-ref-chip"
        @click="emit('code-block-click', block.id)"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>
        </svg>
        <span class="code-ref-chip__lang">{{ block.lang || 'code' }}</span>
        <span class="code-ref-chip__info">· {{ block.content.split('\n').length }} 行 →</span>
      </div>
    </template>
  </div>
</template>

<style>
/* highlight.js token colors - always dark themed */
.hljs-keyword, .hljs-selector-tag, .hljs-literal, .hljs-section, .hljs-link { color: #c792ea; }
.hljs-string, .hljs-title, .hljs-name, .hljs-type, .hljs-attribute, .hljs-symbol, .hljs-bullet, .hljs-addition { color: #c3e88d; }
.hljs-comment, .hljs-quote, .hljs-deletion, .hljs-meta { color: #546e7a; font-style: italic; }
.hljs-number, .hljs-regexp, .hljs-variable, .hljs-template-variable, .hljs-tag .hljs-attr, .hljs-operator { color: #f78c6c; }
.hljs-function, .hljs-class .hljs-title { color: #82aaff; }
.hljs-built_in { color: #ffcb6b; }
.hljs-params { color: #e2e8f0; }
.hljs { background: #13131e; color: #e2e8f0; }

.code-ref-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  padding: 5px 12px;
  margin: 4px 0;
  cursor: pointer;
  font-size: 12px;
  color: var(--text-secondary);
  font-family: var(--font-mono);
  transition: all 0.15s;
  user-select: none;
}
.code-ref-chip:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
  background: var(--bg-active);
}
.code-ref-chip__lang { font-weight: 600; }
.code-ref-chip__info { opacity: 0.7; }
.code-ref-chip svg { width: 13px; height: 13px; }
</style>
