import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: () => import('@/views/HomeView.vue'),
      meta: { title: 'Open DeepWiki - 代码知识库' },
    },
    {
      path: '/repos',
      name: 'repos',
      component: () => import('@/views/RepoListView.vue'),
      meta: { title: '仓库管理 - Open DeepWiki' },
    },
    {
      path: '/wiki/:repoId',
      name: 'wiki',
      component: () => import('@/views/WikiView.vue'),
      props: true,
      meta: { title: 'Wiki - Open DeepWiki' },
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

export default router
