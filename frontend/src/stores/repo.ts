import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { RepositoryItem } from '@/api/repositories'

export const useRepoStore = defineStore('repo', () => {
  const repos = ref<RepositoryItem[]>([])
  const total = ref(0)
  const page = ref(1)
  const perPage = ref(20)
  const isLoading = ref(false)
  const error = ref<string | null>(null)

  function setRepos(items: RepositoryItem[], totalCount: number) {
    repos.value = items
    total.value = totalCount
  }

  function removeRepo(repoId: string) {
    repos.value = repos.value.filter(r => r.id !== repoId)
    total.value = Math.max(0, total.value - 1)
  }

  function updateRepoStatus(repoId: string, status: RepositoryItem['status']) {
    const repo = repos.value.find(r => r.id === repoId)
    if (repo) {
      repo.status = status
    }
  }

  function clearRepos() {
    repos.value = []
    total.value = 0
  }

  return {
    repos,
    total,
    page,
    perPage,
    isLoading,
    error,
    setRepos,
    removeRepo,
    updateRepoStatus,
    clearRepos,
  }
})
