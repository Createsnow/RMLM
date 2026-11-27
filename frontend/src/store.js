import { reactive } from 'vue'
import { ElMessageBox } from 'element-plus'

// 登录凭证存在 localStorage，同一浏览器的所有页面共用一份：
// 任一页面登录、续期、退出，其他页面通过 storage 事件同步（见文末），不会互相把对方挤下线。
const TOKEN = 'rmlm_token'
const USER = 'rmlm_user'

function read(key) {
  try { return localStorage.getItem(key) } catch { return null }
}
function write(key, val) {
  try {
    if (val == null) localStorage.removeItem(key)
    else localStorage.setItem(key, val)
  } catch { /* 隐私模式下忽略，凭证只留在本页内存里 */ }
}
function parse(s) {
  try { return JSON.parse(s || 'null') } catch { return null }
}

export const store = reactive({
  token: read(TOKEN) || '',
  user: parse(read(USER)),
  dicts: {},
  counts: { tasks: 0, notices: 0, by_kind: {} }
})

// 凭证里的账号 ID（只用来判断两份凭证是不是同一个人，不做校验）
export function tokenUid(token) {
  try { return JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))).sub || '' } catch { return '' }
}

export function storedToken() {
  return read(TOKEN) || ''
}

export function setLogin(token, user) {
  store.token = token
  store.user = user
  write(USER, JSON.stringify(user)) // 先写账号再写凭证：其他页面收到凭证变化时账号已经是新的
  write(TOKEN, token)
}

// 服务端续期返回的新凭证（同一账号）
export function setToken(token) {
  store.token = token
  write(TOKEN, token)
}

export function setUser(user) {
  store.user = user
  write(USER, JSON.stringify(user))
}

// 登录页 ?next= 的安全取值：只允许站内路径
export function safeNext(n) {
  return typeof n === 'string' && n.startsWith('/') && !n.startsWith('//') && !n.startsWith('/login') ? n : ''
}

const onLoginPage = () => location.pathname.endsWith('/login')

function toLogin(keepPage) {
  if (onLoginPage()) return
  const here = location.pathname + location.search
  location.href = keepPage && here !== '/' ? `/login?next=${encodeURIComponent(here)}` : '/login'
}

// 主动退出：清掉共用的凭证，所有页面一起退出
export function logout() {
  store.token = ''
  store.user = null
  write(TOKEN, null)
  write(USER, null)
  toLogin(false)
}

// 凭证过期或账号停用（服务端 401）：重新登录后回到当前页面
export function sessionExpired() {
  store.token = ''
  store.user = null
  write(TOKEN, null)
  write(USER, null)
  toLogin(true)
}

let switching = false
// 其他页面改了登录状态时，本页跟着同步
export function syncFromStorage() {
  const token = storedToken()
  if (token === store.token) {
    const u = parse(read(USER))
    if (u && token && String(u.id) === tokenUid(token)) store.user = u
    return
  }
  if (!token) { // 其他页面退出了
    store.token = ''
    store.user = null
    toLogin(true)
    return
  }
  if (!store.token || onLoginPage()) { // 本页还没登录：其他页面登录后，本页直接进入系统
    store.token = token
    store.user = parse(read(USER))
    location.replace(safeNext(new URLSearchParams(location.search).get('next')) || '/')
    return
  }
  const sameUser = tokenUid(token) === tokenUid(store.token)
  store.token = token
  store.user = parse(read(USER)) || (sameUser ? store.user : null)
  if (!sameUser && !switching) { // 其他页面换了账号：刷新本页，免得界面还是上一个账号的
    switching = true
    ElMessageBox.alert('已在其他页面登录了另一个账号，本页将刷新为该账号。', '账号已切换', {
      confirmButtonText: '刷新', showClose: false, callback: () => location.reload()
    })
  }
}

try {
  window.addEventListener('storage', e => {
    if (e.key === null || e.key === TOKEN || e.key === USER) syncFromStorage()
  })
} catch { /* 非浏览器环境 */ }

export const hasRole = (...roles) => !!store.user && roles.some(r => store.user.roles.includes(r))
export const isManager = () => !!store.user?.is_manager
