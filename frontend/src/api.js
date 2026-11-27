import { ElMessage } from 'element-plus'
import { store, adoptToken, latestToken, sessionExpired, setToken, tokenUid } from './store'

// opts.anon：不带本页签的登录凭证（登录接口用：切换账号时输错密码不能把本页签当前的账号退出）
async function request(method, url, body, opts = {}) {
  const headers = {}
  const sent = opts.anon ? '' : store.token
  if (sent) headers.Authorization = `Bearer ${sent}`
  let payload
  if (body instanceof FormData) payload = body
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  const resp = await fetch(url, { method, headers, body: payload })
  // 服务端滑动续期：换上新凭证（期间本页签已换了凭证就不覆盖）
  const renewed = resp.headers.get('X-New-Token')
  if (renewed && sent && store.token === sent) setToken(renewed)
  if (resp.status === 401 && sent) {
    // 本页签期间已换了凭证，或同一账号在其他页签续期、改了密码：换上最新凭证重试一次，不要把这个账号退出
    const uid = tokenUid(sent)
    const latest = store.token !== sent ? store.token : latestToken(uid)
    if (!opts.retried && latest && latest !== sent && tokenUid(latest) === uid) {
      if (store.token === sent) adoptToken(latest)
      return request(method, url, body, { ...opts, retried: true })
    }
    if (store.token === sent) sessionExpired(sent)
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
