import { config } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, vi } from 'vitest'

// Setup pinia before each test
beforeEach(() => {
  setActivePinia(createPinia())
})

// Mock CSS variables (jsdom doesn't support them fully)
Object.defineProperty(window, 'getComputedStyle', {
  value: () => ({
    getPropertyValue: () => '',
  }),
})

// Mock EventSource
class MockEventSource {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 2
  readyState = MockEventSource.CONNECTING
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  onopen: ((event: Event) => void) | null = null
  url: string
  constructor(url: string) {
    this.url = url
  }
  close() {
    this.readyState = MockEventSource.CLOSED
  }
  dispatchEvent(_event: Event) {
    return true
  }
  addEventListener(_type: string, _listener: EventListenerOrEventListenerObject) {}
  removeEventListener(_type: string, _listener: EventListenerOrEventListenerObject) {}
}

global.EventSource = MockEventSource as unknown as typeof EventSource

// Silence console.warn in tests
vi.spyOn(console, 'warn').mockImplementation(() => {})
