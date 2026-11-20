import { ElMessage } from 'element-plus'
import { store, logout } from './store'

async function request(method, url, body, opts = {}) {
  const headers = {}
  if (store.token) headers.Authorization = `Bearer ${store.token}`
  let payload
  if (body instanceof FormData) payload = body
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  const resp = await fetch(url, { method, headers, body: payload })
  if (opts.raw) return resp
  let data = null
  try { data = await resp.json() } catch { data = null }
  if (!resp.ok) {
    const msg = (data && (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail))) || `请求失败（${resp.status}）`
    if (resp.status === 401) logout()
    if (!opts.silent) ElMessage.error(msg)
    const err = new Error(msg)
    err.status = resp.status
    throw err
  }
  return data
}

export const api = {
  get: (url, opts) => request('GET', url, undefined, opts),
  post: (url, body, opts) => request('POST', url, body ?? {}, opts),
  put: (url, body, opts) => request('PUT', url, body, opts),
  del: (url, opts) => request('DELETE', url, undefined, opts),
  upload: (url, file) => { const fd = new FormData(); fd.append('file', file); return request('POST', url, fd) },
  // 带鉴权读取图片，返回 objectURL
  async image(id) {
    const resp = await request('GET', `/api/photos/${id}`, undefined, { raw: true })
    if (!resp.ok) return ''
    return URL.createObjectURL(await resp.blob())
  }
}
