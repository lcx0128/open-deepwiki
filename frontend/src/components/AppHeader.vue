<script setup lang="ts">
import { ref } from 'vue'

const isDark = ref(false)

function toggleTheme() {
  isDark.value = !isDark.value
  document.documentElement.setAttribute('data-theme', isDark.value ? 'dark' : 'light')
}
</script>

<template>
  <header class="app-header">
    <div class="header-content">
      <div class="header-left">
        <RouterLink to="/" class="header-logo">
          <span class="logo-icon">📚</span>
          <span class="logo-text">Open DeepWiki</span>
        </RouterLink>
        <nav class="header-nav">
          <RouterLink to="/" class="nav-link">首页</RouterLink>
          <RouterLink to="/repos" class="nav-link">仓库管理</RouterLink>
        </nav>
      </div>
      <div class="header-right">
        <button class="btn btn-ghost btn-sm" @click="toggleTheme">
          {{ isDark ? '☀️' : '🌙' }}
        </button>
      </div>
    </div>
  </header>
</template>

<style scoped>
.app-header {
  height: var(--header-height);
  background: var(--bg-primary);
  border-bottom: 1px solid var(--border-color);
  position: sticky;
  top: 0;
  z-index: 100;
  box-shadow: var(--shadow-sm);
}

.header-content {
  max-width: 1440px;
  margin: 0 auto;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 24px;
}

.header-logo {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 700;
  font-size: var(--font-size-lg);
  color: var(--text-primary);
  text-decoration: none;
}

.logo-icon { font-size: 20px; }

.logo-text {
  background: linear-gradient(135deg, #2563eb, #7c3aed);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.header-nav {
  display: flex;
  align-items: center;
  gap: 4px;
}

.nav-link {
  padding: 6px 12px;
  border-radius: var(--radius);
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  text-decoration: none;
  transition: all 0.15s;
}

.nav-link:hover { background: var(--bg-hover); color: var(--text-primary); }
.nav-link.router-link-active { background: var(--bg-active); color: var(--color-primary); font-weight: 500; }

.header-right { display: flex; align-items: center; gap: 8px; }

@media (max-width: 768px) {
  .header-nav { display: none; }
  .logo-text { display: none; }
}
</style>
