<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'

const isDark = ref(false)
const mobileMenuOpen = ref(false)

function applyTheme(dark: boolean) {
  isDark.value = dark
  document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light')
  localStorage.setItem('theme', dark ? 'dark' : 'light')
}

function toggleTheme() {
  applyTheme(!isDark.value)
}

function toggleMobileMenu() {
  mobileMenuOpen.value = !mobileMenuOpen.value
}

function closeMobileMenu() {
  mobileMenuOpen.value = false
}

function handleOverlayClick() {
  mobileMenuOpen.value = false
}

onMounted(() => {
  const saved = localStorage.getItem('theme')
  if (saved === 'dark') {
    applyTheme(true)
  } else if (saved === 'light') {
    applyTheme(false)
  } else {
    // system preference
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    applyTheme(prefersDark)
  }
})

onUnmounted(() => {
  mobileMenuOpen.value = false
})
</script>

<template>
  <header class="app-header">
    <div class="header-content">
      <div class="header-left">
        <RouterLink to="/" class="header-logo">
          <svg class="logo-icon" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M9 7h6M9 11h4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          </svg>
          <span class="logo-text">Open DeepWiki</span>
        </RouterLink>
        <nav class="header-nav">
          <RouterLink to="/" class="nav-link">首页</RouterLink>
          <RouterLink to="/repos" class="nav-link">仓库</RouterLink>
          <RouterLink to="/system" class="nav-link">系统管理</RouterLink>
        </nav>
      </div>
      <div class="header-right">
        <button class="theme-toggle" @click="toggleTheme" :aria-label="isDark ? '切换为亮色' : '切换为暗色'">
          <!-- Sun icon (show when dark, clicking switches to light) -->
          <svg v-if="isDark" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <circle cx="12" cy="12" r="5" stroke="currentColor" stroke-width="1.5"/>
            <path d="M12 2v2M12 20v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M2 12h2M20 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          </svg>
          <!-- Moon icon (show when light, clicking switches to dark) -->
          <svg v-else viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </button>

        <!-- Hamburger button: only visible on mobile -->
        <button
          class="hamburger-btn"
          @click="toggleMobileMenu"
          :aria-label="mobileMenuOpen ? '关闭菜单' : '打开菜单'"
          :aria-expanded="mobileMenuOpen"
        >
          <!-- X icon when open -->
          <svg v-if="mobileMenuOpen" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          <!-- Hamburger icon when closed -->
          <svg v-else viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M4 6h16M4 12h16M4 18h16" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </button>
      </div>
    </div>

    <!-- Mobile nav dropdown -->
    <div v-if="mobileMenuOpen" class="mobile-nav" role="navigation" aria-label="移动端导航">
      <RouterLink to="/" class="mobile-nav-link" @click="closeMobileMenu">首页</RouterLink>
      <RouterLink to="/repos" class="mobile-nav-link" @click="closeMobileMenu">仓库</RouterLink>
      <RouterLink to="/system" class="mobile-nav-link" @click="closeMobileMenu">系统管理</RouterLink>
    </div>

    <!-- Overlay backdrop -->
    <div v-if="mobileMenuOpen" class="mobile-overlay" @click="handleOverlayClick" aria-hidden="true" />
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
}

/* When mobile menu is open the header expands, so remove fixed height constraint */
.app-header:has(.mobile-nav) {
  height: auto;
}

.header-content {
  max-width: 1600px;
  margin: 0 auto;
  height: var(--header-height);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 28px;
}

.header-logo {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 15px;
  color: var(--text-primary);
  text-decoration: none;
}

.logo-icon {
  width: 22px;
  height: 22px;
  color: var(--color-primary);
  flex-shrink: 0;
}

.logo-text {
  color: var(--text-primary);
  letter-spacing: -0.01em;
}

.header-nav {
  display: flex;
  align-items: center;
  gap: 2px;
}

.nav-link {
  padding: 5px 10px;
  border-radius: var(--radius);
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  text-decoration: none;
  transition: all 0.15s;
  font-weight: 500;
}

.nav-link:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
  text-decoration: none;
}

.nav-link.router-link-active {
  color: var(--text-primary);
  font-weight: 500;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.theme-toggle {
  width: 34px;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  cursor: pointer;
  color: var(--text-tertiary);
  transition: all 0.15s;
  padding: 0;
}

.theme-toggle:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
  border-color: var(--border-color-strong);
}

.theme-toggle svg {
  width: 16px;
  height: 16px;
}

/* Hamburger button: hidden by default, visible only on mobile */
.hamburger-btn {
  display: none;
  width: 34px;
  height: 34px;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  cursor: pointer;
  color: var(--text-tertiary);
  transition: all 0.15s;
  padding: 0;
}

.hamburger-btn:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
  border-color: var(--border-color-strong);
}

.hamburger-btn svg {
  width: 18px;
  height: 18px;
}

/* Mobile nav dropdown */
.mobile-nav {
  display: none;
  flex-direction: column;
  background: var(--bg-primary);
  border-top: 1px solid var(--border-color);
  padding: 8px 0;
}

.mobile-nav-link {
  display: flex;
  align-items: center;
  min-height: 44px;
  padding: 0 20px;
  font-size: var(--font-size-md);
  font-weight: 500;
  color: var(--text-secondary);
  text-decoration: none;
  transition: background 0.15s, color 0.15s;
  border-left: 3px solid transparent;
}

.mobile-nav-link:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
  text-decoration: none;
}

.mobile-nav-link.router-link-active {
  color: var(--color-primary);
  border-left-color: var(--color-primary);
  background: var(--color-primary-light);
}

[data-theme="dark"] .mobile-nav-link.router-link-active {
  background: rgba(37, 99, 235, 0.1);
}

/* Overlay backdrop */
.mobile-overlay {
  display: none;
  position: fixed;
  inset: 0;
  top: 0;
  background: rgba(0, 0, 0, 0.3);
  z-index: -1;
}

@media (max-width: 768px) {
  .header-nav {
    display: none;
  }

  .hamburger-btn {
    display: flex;
  }

  .mobile-nav {
    display: flex;
  }

  .mobile-overlay {
    display: block;
  }
}
</style>
