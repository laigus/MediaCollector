import { computed, onMounted, onUnmounted, ref } from 'vue'
import api from './api'

export function useDownload(platform) {
  const task = ref(null)
  const submitting = ref(false)
  const error = ref('')
  const pollError = ref('')
  const running = computed(() => task.value?.status === 'running')
  const key = 'media-collector.last-download.' + platform
  let timer
  let disposed = false
  let trackedId = null

  function remember(id) {
    trackedId = id ? String(id) : null
    if (trackedId) localStorage.setItem(key, trackedId)
    else localStorage.removeItem(key)
  }

  function setTask(next) {
    task.value = next
    pollError.value = ''
    clearTimeout(timer)
    if (next?.status === 'running' && !disposed) {
      timer = setTimeout(() => refresh(next.id), 1200)
    }
  }

  async function refresh(id) {
    try {
      const next = await api.task(id)
      if (!disposed && trackedId === String(id) && next.provider === platform) setTask(next)
    } catch (err) {
      if (disposed || trackedId !== String(id)) return
      if (err.status === 404) {
        remember(null)
        setTask(null)
      } else {
        pollError.value = err.message
        timer = setTimeout(() => refresh(id), 3500)
      }
    }
  }

  async function submit(action) {
    if (submitting.value || running.value) return false
    submitting.value = true
    error.value = ''
    try {
      const next = await action()
      remember(next.id)
      setTask(next)
      return true
    } catch (err) {
      error.value = err.message
      return false
    } finally {
      submitting.value = false
    }
  }

  function dismiss() {
    remember(null)
    setTask(null)
  }

  onMounted(() => {
    trackedId = localStorage.getItem(key)
    if (trackedId) refresh(trackedId)
  })
  onUnmounted(() => {
    disposed = true
    clearTimeout(timer)
  })
  return { task, submitting, error, pollError, running, submit, dismiss }
}
