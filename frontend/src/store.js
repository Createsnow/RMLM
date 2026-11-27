import { reactive } from 'vue'

/*
 * 登录状态：同一台电脑、同一个浏览器里可以同时登录多个账号，每个浏览器页签用自己的账号，互不影响。
 *
 * - 本页签用哪个账号：存在 sessionStorage（只属于这个页签；F5 刷新保留，关掉页签即清除；
 *   复制页签、从本页打开的新窗口沿用本页的账号）。
 * - 本浏览器已登录的账号：存在 localStorage，每个账号一项（rmlm_acct_<账号 ID>，存凭证和账号信息）。用来：
 *   新开的页签自动进入最近使用的账号；登录页列出已登录的账号，点一下就切换；
 *   同一账号的多个页签同步续期、改密码、退出（storage 事件，见 syncFromStorage）。
 * - 退出、凭证过期只影响这个账号：用这个账号的页签一起回到登录页，其他账号的页签照常使用。
 */
const SESSION = 'rmlm_session' // sessionStorage：本页签的 { token, user }
const OPENED = 'rmlm_tab' // sessionStorage：本页签已经打开过（没有它的是新开的页签）
const PREV = 'rmlm_prev_uid' // sessionStorage：本页签退出 / 过期前用的账号，登录页等这个账号重新登录
const ACCT = 'rmlm_acct_' // localStorage：已登录账号，后接账号 ID
const LAST = 'rmlm_last_uid' // localStorage：最近使用的账号（新开的页签默认进入）
const OLD_TOKEN = 'rmlm_token' // 旧版本（整个浏览器共用一个账号）的存法，启动时迁移过来
const OLD_USER = 'rmlm_user'
const EXPIRED_GRACE = 10 * 60 * 1000 // 本机时钟可能有偏差：过期 10 分钟后才从账号列表里去掉

function area(name) {
  try { return name === 'local' ? window.localStorage : window.sessionStorage } catch { return null }
}
function get(name, key) {
  try { return area(name)?.getItem(key) ?? null } catch { return null }
}
/** 写入成功返回 true；隐私模式、存储已满等写不进去时返回 false。 */
function put(name, key, val) {
  try {
    const s = area(name)
    if (!s) return false
    if (val == null) s.removeItem(key)
    else s.setItem(key, val)
    return true
  } catch { return false }
}
function parse(s) {
  try { return JSON.parse(s || 'null') } catch { return null }
}

/** 凭证内容（只读里面的账号 ID、过期时间，不做校验，校验在服务端） */
function claims(token) {
  try { return JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))) || {} } catch { return {} }
}
/** 凭证里的账号 ID（字符串）。 */
export function tokenUid(token) {
  return token ? String(claims(token).sub || '') : ''
}
const expired = token => {
  const exp = Number(claims(token).exp)
  return !!exp && exp * 1000 < Date.now() - EXPIRED_GRACE
}

// localStorage 不可用（部分浏览器的隐私模式）时退回各页签各自登录：不能多页签共用、不能同步
let shared = put('local', '__rmlm_probe', '1') && put('local', '__rmlm_probe', null)

// ---------------------------------------------------------------- 已登录账号（全浏览器共用）

function readAcct(uid) {
  if (!shared || !uid) return null
  const a = parse(get('local', ACCT + uid))
  return a && typeof a.token === 'string' && tokenUid(a.token) === String(uid) ? a : null
}
function writeAcct(uid, a) {
  return shared && put('local', ACCT + uid, a ? JSON.stringify(a) : null)
}
function acctKeys() {
  const keys = []
  try {
    const s = area('local')
    for (let i = 0; s && i < s.length; i++) {
      const k = s.key(i)
      if (k && k.startsWith(ACCT)) keys.push(k)
    }
  } catch { /* ignore */ }
  return keys
}
/** 本浏览器已登录的账号，最近登录的在前；顺带清掉已过期、内容不对的项。 */
function listAccounts() {
  if (!shared) return []
  const out = []
  for (const k of acctKeys()) {
    const uid = k.slice(ACCT.length)
    const a = readAcct(uid)
    if (!a || expired(a.token)) { put('local', k, null); continue }
    out.push({ uid, ...a })
  }
  return out.sort((x, y) => (y.at || 0) - (x.at || 0))
}

/** 旧版本把唯一的账号存在 rmlm_token / rmlm_user：升级后已登录的用户不用重新登录。 */
function migrate() {
  const token = get('local', OLD_TOKEN)
  if (!token && !get('local', OLD_USER)) return
  const uid = tokenUid(token)
  if (shared && uid && !expired(token) && !readAcct(uid)) {
    const user = parse(get('local', OLD_USER))
    writeAcct(uid, { token, user: user && String(user.id) === uid ? user : null, at: Date.now() })
    if (!get('local', LAST)) put('local', LAST, uid)
  }
  put('local', OLD_TOKEN, null)
  put('local', OLD_USER, null)
}

// ---------------------------------------------------------------- 本页签

export const store = reactive({
  token: '',
  user: null,
  /** 本浏览器已登录的账号 [{ uid, name, username }]（登录页切换账号、页签标题用） */
  accounts: [],
  dicts: {},
  counts: { tasks: 0, notices: 0, by_kind: {} }
})

// 本页面（这次加载）最先使用的账号：换成别的账号时整页重新加载，免得界面里留着上一个账号的数据
let pageUid = ''

function saveSession() {
  put('session', SESSION, store.token ? JSON.stringify({ token: store.token, user: store.user }) : null)
}
function sameJson(a, b) {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null)
}
/** 本页签换上这个凭证（只改本页签，不动已登录账号列表） */
function apply(token, user) {
  const uid = tokenUid(token)
  if (!pageUid) pageUid = uid
  store.token = token
  if (!sameJson(store.user, user)) store.user = user
  saveSession()
}
function clearTab(uid) {
  store.token = ''
  store.user = null
  saveSession()
  if (uid) put('session', PREV, uid)
}
function refreshAccounts() {
  const list = listAccounts().map(a => ({ uid: a.uid, name: a.user?.name || '', username: a.user?.username || '' }))
  if (!sameJson(store.accounts, list)) store.accounts = list
}
function markActive() {
  const uid = tokenUid(store.token)
  if (uid && shared && get('local', LAST) !== uid && readAcct(uid)) put('local', LAST, uid)
}

const onLoginPage = () => location.pathname.endsWith('/login')
/** 登录页「切换账号」模式（/login?switch=1）：本页签要登录另一个账号，不自动进入 */
export const switchMode = () => onLoginPage() && new URLSearchParams(location.search).has('switch')

function init() {
  migrate()
  const fresh = !get('session', OPENED)
  put('session', OPENED, '1')
  const s = parse(get('session', SESSION))
  const uid = tokenUid(s?.token)
  if (uid) {
    if (!shared) apply(s.token, s.user)
    else {
      // 同一账号在其他页签续期、改了密码，用最新的凭证；这个账号已在其他页签退出了，本页签也退出
      const a = readAcct(uid)
      if (a) apply(a.token, a.user || s.user)
      else clearTab(uid)
    }
  } else if (fresh && !switchMode()) {
    // 新开的页签：进入最近使用的账号（想用别的账号，点左下角账号菜单「切换账号」）；
    // 「新页签登录其他账号」打开的登录页不进入，直接登录新账号
    const list = listAccounts()
    const a = readAcct(get('local', LAST)) || list[0]
    if (a) apply(a.token, a.user)
  }
  refreshAccounts()
}
init()

/** 当前这个账号在本浏览器里最新的凭证（其他页签可能已续期或改了密码） */
export function latestToken(uid) {
  return readAcct(uid)?.token || ''
}

/** 本页签换上同一账号的另一份有效凭证（来自已登录账号列表，不再回写） */
export function adoptToken(token) {
  apply(token, store.user)
}

/** 本页签登录（账号密码 / 企业微信），也用于改密码后换发的新凭证。 */
export function setLogin(token, user) {
  const uid = tokenUid(token)
  apply(token, user)
  put('session', PREV, null)
  if (shared) {
    // 存不进去（存储已满等）时本页签照常使用，只是不能和其他页签共用
    if (!writeAcct(uid, { token, user, at: Date.now() })) shared = false
    else put('local', LAST, uid)
  }
  refreshAccounts()
}

/** 服务端续期返回的新凭证（同一账号）：同一账号的其他页签也换上 */
export function setToken(token) {
  apply(token, store.user)
  const uid = tokenUid(token)
  const a = readAcct(uid)
  if (a && a.token !== token) writeAcct(uid, { ...a, token })
}

export function setUser(user) {
  if (!sameJson(store.user, user)) store.user = user
  saveSession()
  const uid = tokenUid(store.token)
  const a = readAcct(uid)
  if (a && user && String(user.id) === uid && !sameJson(a.user, user)) writeAcct(uid, { ...a, user })
  refreshAccounts()
}

/** 登录页点已登录的账号：本页签改用这个账号。账号已失效返回 false。 */
export function useAccount(uid) {
  const a = readAcct(uid)
  if (!a || expired(a.token)) { refreshAccounts(); return false }
  apply(a.token, a.user)
  put('session', PREV, null)
  put('local', LAST, String(uid))
  return true
}

/** 退出某个已登录的账号（登录页账号列表里的「退出」）；是本页签的账号时本页签也退出。 */
export function forgetAccount(uid) {
  uid = String(uid)
  writeAcct(uid, null)
  if (get('local', LAST) === uid) put('local', LAST, null)
  if (tokenUid(store.token) === uid) clearTab(uid)
  refreshAccounts()
}

/** 本页签换了账号（与本页面最先用的不同）：进入系统时要整页重新加载 */
export const accountChanged = () => !!pageUid && pageUid !== tokenUid(store.token)

// 登录页 ?next= 的安全取值：只允许站内路径
export function safeNext(n) {
  return typeof n === 'string' && n.startsWith('/') && !n.startsWith('//') && !n.startsWith('/login') ? n : ''
}


function toLogin(keepPage) {
  if (onLoginPage()) return
  const here = location.pathname + location.search
  location.href = keepPage && here !== '/' ? `/login?next=${encodeURIComponent(here)}` : '/login'
}

/** 主动退出：退出本页签的账号；用这个账号的其他页签一起退出，其他账号不受影响 */
export function logout() {
  const uid = tokenUid(store.token)
  if (uid) forgetAccount(uid)
  else clearTab('')
  toLogin(false)
}

/** 凭证过期或账号停用（服务端 401）：这个账号退出，重新登录后回到当前页面 */
export function sessionExpired(sent = store.token) {
  const uid = tokenUid(sent)
  const a = readAcct(uid)
  if (a && a.token === sent) { // 列表里是更新的凭证时说明其他页签还能用，不去掉
    writeAcct(uid, null)
    if (get('local', LAST) === uid) put('local', LAST, null)
  }
  clearTab(uid)
  refreshAccounts()
  toLogin(true)
}

/** 其他页签改了已登录账号（登录、续期、改密码、退出）时，本页签跟着同步 */
export function syncFromStorage() {
  refreshAccounts()
  if (!shared) return
  if (store.token) {
    const uid = tokenUid(store.token)
    const a = readAcct(uid)
    if (!a) { // 本页签的账号在其他页签退出了
      clearTab(uid)
      toLogin(true)
      return
    }
    apply(a.token, a.user && String(a.user.id) === uid ? a.user : store.user)
    return
  }
  // 本页签没登录、停在登录页：等的账号（本页签从没登录过时为任一账号）在其他页签登录后，直接进入系统
  if (!onLoginPage() || switchMode() || new URLSearchParams(location.search).has('code')) return
  const prev = get('session', PREV)
  const a = prev ? readAcct(prev) : listAccounts()[0]
  if (!a) return
  apply(a.token, a.user)
  put('session', PREV, null)
  location.replace(safeNext(new URLSearchParams(location.search).get('next')) || '/')
}

try {
  window.addEventListener('storage', e => {
    if (e.key === null || (e.key && e.key.startsWith(ACCT))) syncFromStorage()
  })
  // 切回本页签：记为最近使用的账号（新开的页签进入它），顺带补一次同步（后台页签可能错过了通知）
  window.addEventListener('focus', markActive)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') { syncFromStorage(); markActive() }
  })
  if (document.visibilityState === 'visible' && document.hasFocus()) markActive()
} catch { /* 非浏览器环境 */ }

export const hasRole = (...roles) => !!store.user && roles.some(r => store.user.roles.includes(r))
export const isManager = () => !!store.user?.is_manager
