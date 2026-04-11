import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.DEV ? 'http://localhost:8000' : '',
  timeout: 30000,
})

export default {
  // Tasks
  listTasks: () => api.get('/api/tasks'),
  getTask: (id) => api.get(`/api/tasks/${id}`),
  createTask: (data) => api.post('/api/tasks', data),
  startTask: (id) => api.post(`/api/tasks/${id}/start`),
  deleteTask: (id) => api.delete(`/api/tasks/${id}`),
  getFailedList: (id) => api.get(`/api/tasks/${id}/failed`),
  retryFailed: (id) => api.post(`/api/tasks/${id}/retry-failed`, null, { timeout: 300000 }),

  // Providers
  listProviders: () => api.get('/api/providers'),
  getProviderConfig: (name) => api.get(`/api/providers/${name}/config`),
  updateProviderConfig: (name, config) =>
    api.put(`/api/providers/${name}/config`, { provider: name, config }),
  getCollectsList: (name) => api.get(`/api/providers/${name}/collects`, { timeout: 300000 }),

  // Health
  health: () => api.get('/api/health'),
}
