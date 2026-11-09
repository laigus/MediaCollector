async function request(path, { method = 'GET', body, timeout = 30000, signal } = {}) {
  const deadline = AbortSignal.timeout(timeout)
  const response = await fetch('/api' + path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    signal: signal ? AbortSignal.any([signal, deadline]) : deadline,
  }).catch((error) => {
    if (error.name === 'AbortError') throw error
    throw new Error(error.name === 'TimeoutError' ? '读取超时，请重试' : '连接中断，请检查本地服务')
  })
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    const error = new Error(typeof data?.detail === 'string' ? data.detail : '操作失败，请检查输入后重试')
    error.status = response.status
    throw error
  }
  return data
}

export default {
  destination: (name, source) => request(
    '/providers/' + name + '/destination' + (source ? '?' + new URLSearchParams({ source }) : ''),
  ),
  login: (name) => request('/providers/' + name + '/login', { method: 'POST', timeout: 240000 }),
  douyinSession: () => request('/providers/douyin/session'),
  logoutDouyin: () => request('/providers/douyin/logout', { method: 'POST' }),
  chooseDownloadFolder: (name, source) =>
    request('/providers/' + name + '/destination', { method: 'POST', body: { source }, timeout: 310000 }),
  folders: () => request('/providers/douyin/collects', { timeout: 480000 }),
  works: (source, refresh, signal) => request(
    '/providers/douyin/works?' + new URLSearchParams({ source, refresh }),
    { timeout: 480000, signal },
  ),
  downloadDouyin: (body) => request('/providers/douyin/downloads', { method: 'POST', body }),
  downloadWeibo: (body) => request('/tasks', { method: 'POST', body }),
  task: (id) => request('/tasks/' + id),
  failed: (id) => request('/tasks/' + id + '/failed'),
  retry: (id) => request('/tasks/' + id + '/retry-failed', { method: 'POST' }),
}
