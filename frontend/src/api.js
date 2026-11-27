import { ElMessage } from 'element-plus'
import { store, setToken, sessionExpired, storedToken, syncFromStorage, tokenUid } from './store'

async function request(method, url, body, opts = {}) {
  const headers = {}
  const sent = store.token
  if (sent) headers.Authorization = `Bearer ${sent}`
  let payload
  if (body instanceof FormData) payload = body
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  const resp = await fetch(url, { method, headers, body: payload })
  // 服务端滑动续期：换上新凭证（期间本页已换了凭证就不覆盖）
  const renewed = resp.headers.get('X-New-Token')
  if (renewed && sent && store.token === sent) setToken(renewed)
  if (resp.status === 401 && sent) {
    // 其他页面已经重新登录或续期过：换上最新凭证重试一次，不要把其他页面的登录清掉
    const latest = storedToken() || store.token
    if (latest !== sent) {
      if (!opts.retried && tokenUid(latest) === tokenUid(sent)) {
        store.token = latest
        return request(method, url, body, { ...opts, retried: true })
      }
      syncFromStorage() // 其他页面换了账号：按那边的登录状态刷新本页
    } else sessionExpired()
  }
  if (opts.raw) return resp
  let data = null
  try { data = await resp.json() } catch { data = null }
  if (!resp.ok) {
    const msg = (data && (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail))) || `请求失败（${resp.status}）`
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
