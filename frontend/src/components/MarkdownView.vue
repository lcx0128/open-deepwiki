<script setup lang="ts">
import { computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import MermaidBlock from './MermaidBlock.vue'

const props = defineProps<{ content: string }>()

interface Block {
  type: 'html' | 'mermaid'
  content: string
  id: string
}

const parsedBlocks = computed<Block[]>(() => {
  const blocks: Block[] = []
  let counter = 0

  if (!props.content) return blocks

  // 分割 ```mermaid 代码块
  const parts = props.content.split(/(```mermaid\s*\n[\s\S]*?\n\s*```)/g)

  for (const part of parts) {
    const mermaidMatch = part.match(/^```mermaid\s*\n([\s\S]*?)\n\s*```$/)
    if (mermaidMatch) {
      blocks.push({
        type: 'mermaid',
        content: mermaidMatch[1].trim(),
        id: `md-mermaid-${counter++}`,
      })
    } else if (part.trim()) {
      const html = DOMPurify.sanitize(
        marked.parse(part, { breaks: true, gfm: true }) as string
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
</script>

<template>
  <div class="markdown-view">
    <template v-for="block in parsedBlocks" :key="block.id">
      <div
        v-if="block.type === 'html'"
        class="markdown-body"
        v-html="block.content"
      />
      <MermaidBlock
        v-else
        :code="block.content"
        :id="block.id"
      />
    </template>
  </div>
</template>
