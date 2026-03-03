<script setup lang="ts">
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const password = ref('')
const showPassword = ref(false)
const errorMsg = ref('')
const loading = ref(false)

async function handleLogin() {
  if (!password.value.trim()) {
    errorMsg.value = '请输入登录口令'
    return
  }
  loading.value = true
  errorMsg.value = ''
  try {
    await authStore.login(password.value)
    const redirect = (route.query.redirect as string) || '/'
    router.replace(redirect)
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    errorMsg.value = detail || '口令错误，请重试'
    password.value = ''
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-logo">
        <span class="logo-icon">📖</span>
        <span class="logo-text">Open DeepWiki</span>
      </div>

      <h1 class="login-title">访问验证</h1>
      <p class="login-desc">请输入访问口令以继续</p>

      <form class="login-form" @submit.prevent="handleLogin">
        <div class="input-wrapper">
          <input
            :type="showPassword ? 'text' : 'password'"
            v-model="password"
            placeholder="输入访问口令"
            class="password-input"
            :class="{ 'input-error': errorMsg }"
            autofocus
            autocomplete="current-password"
          />
          <button
            type="button"
            class="toggle-btn"
            @click="showPassword = !showPassword"
            tabindex="-1"
            :title="showPassword ? '隐藏口令' : '显示口令'"
          >
            <svg v-if="showPassword" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94"/>
              <path d="M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19"/>
              <line x1="1" y1="1" x2="23" y2="23"/>
            </svg>
            <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
              <circle cx="12" cy="12" r="3"/>
            </svg>
          </button>
        </div>

        <p v-if="errorMsg" class="error-msg">{{ errorMsg }}</p>

        <button type="submit" class="login-btn" :disabled="loading">
          <span v-if="loading" class="spinner"></span>
          <span>{{ loading ? '验证中…' : '登录' }}</span>
        </button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  flex: 1;
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-primary);
  padding: 24px;
}

.login-card {
  width: 100%;
  max-width: 380px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-xl);
  padding: 40px 36px;
  box-shadow: var(--shadow-lg);
}

.login-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 28px;
}

.logo-icon {
  font-size: 24px;
}

.logo-text {
  font-size: var(--font-size-lg);
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: -0.3px;
}

.login-title {
  font-size: var(--font-size-2xl);
  font-weight: 700;
  color: var(--text-primary);
  margin: 0 0 8px;
  letter-spacing: -0.5px;
}

.login-desc {
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  margin: 0 0 28px;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.input-wrapper {
  position: relative;
}

.password-input {
  width: 100%;
  height: 44px;
  padding: 0 44px 0 14px;
  background: var(--bg-primary);
  border: 1px solid var(--border-color-strong);
  border-radius: var(--radius);
  color: var(--text-primary);
  font-size: var(--font-size-base);
  font-family: var(--font-sans);
  outline: none;
  transition: border-color 0.15s, box-shadow 0.15s;
  box-sizing: border-box;
}

.password-input::placeholder {
  color: var(--text-muted);
}

.password-input:focus {
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12);
}

.password-input.input-error {
  border-color: var(--color-error);
}

.toggle-btn {
  position: absolute;
  right: 12px;
  top: 50%;
  transform: translateY(-50%);
  background: none;
  border: none;
  cursor: pointer;
  padding: 4px;
  color: var(--text-muted);
  display: flex;
  align-items: center;
}

.toggle-btn:hover {
  color: var(--text-secondary);
}

.toggle-btn svg {
  width: 18px;
  height: 18px;
}

.error-msg {
  font-size: var(--font-size-sm);
  color: var(--color-error);
  margin: 0;
}

.login-btn {
  height: 44px;
  background: var(--color-primary);
  color: #fff;
  border: none;
  border-radius: var(--radius);
  font-size: var(--font-size-base);
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s, opacity 0.15s;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: 4px;
}

.login-btn:hover:not(:disabled) {
  background: var(--color-primary-dark);
}

.login-btn:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.spinner {
  width: 16px;
  height: 16px;
  border: 2px solid rgba(255,255,255,0.35);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
