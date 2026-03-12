import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { title: '登录 - Open DeepWiki', public: true },
    },
    {
      path: '/',
      name: 'home',
      component: () => import('@/views/HomeView.vue'),
      meta: { title: 'Open DeepWiki - 代码知识库', guestAllowed: true },
    },
    {
      path: '/repos',
      name: 'repos',
      component: () => import('@/views/RepoListView.vue'),
      meta: { title: '仓库管理 - Open DeepWiki', guestAllowed: true },
    },
    {
      path: '/system',
      name: 'system',
      component: () => import('@/views/SystemView.vue'),
      meta: { title: '系统管理 - Open DeepWiki' },
    },
    {
      path: '/wiki/:repoId',
      name: 'wiki',
      component: () => import('@/views/WikiView.vue'),
      props: true,
      meta: { title: 'Wiki - Open DeepWiki', guestAllowed: true },
    },
    {
      path: '/chat/:repoId/:sessionId?',
      name: 'chat',
      component: () => import('@/views/ChatView.vue'),
      props: true,
      meta: { title: '对话 - Open DeepWiki' },
    },
  ],
})

// 更新页面标题
router.afterEach((to) => {
  if (to.meta?.title) {
    document.title = to.meta.title as string
  }
})

// 鉴权守卫：仅在首次导航时初始化 auth 状态，之后每次导航检查登录
let authInitialized = false

router.beforeEach(async (to) => {
  const authStore = useAuthStore()

  if (!authInitialized) {
    await authStore.checkStatus()
    authInitialized = true
  }

  // 鉴权未启用，全部放行
  if (!authStore.authEnabled) return true

  // 登录页等公开路由，直接放行
  if (to.meta?.public) return true

  // 允许访客访问的路由（home/repos/wiki），直接放行，视图内部处理权限
  if (to.meta?.guestAllowed) return true

  // 未登录 → 跳转登录页，并记录目标路径以便登录后重定向
  if (!authStore.isAuthenticated) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  // 已登录访问登录页 → 跳回首页
  if (to.name === 'login') return { name: 'home' }

  return true
})

export default router
