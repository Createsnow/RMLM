import { reactive } from 'vue'

function load() {
  try { return JSON.parse(localStorage.getItem('rmlm_user') || 'null') } catch { return null }
}

export const store = reactive({
  token: (() => { try { return localStorage.getItem('rmlm_token') || '' } catch { return '' } })(),
  user: load(),
  dicts: {},
  counts: { tasks: 0, notices: 0, by_kind: {} }
})

export function setLogin(token, user) {
  store.token = token
  store.user = user
  try {
    localStorage.setItem('rmlm_token', token)
    localStorage.setItem('rmlm_user', JSON.stringify(user))
  } catch { /* 隐私模式下忽略 */ }
}

export function setUser(user) {
  store.user = user
  try { localStorage.setItem('rmlm_user', JSON.stringify(user)) } catch { /* ignore */ }
}

export function logout() {
  store.token = ''
  store.user = null
  try { localStorage.removeItem('rmlm_token'); localStorage.removeItem('rmlm_user') } catch { /* ignore */ }
  if (!location.pathname.endsWith('/login')) location.href = '/login'
}

export const hasRole = (...roles) => !!store.user && roles.some(r => store.user.roles.includes(r))
export const isManager = () => !!store.user?.is_manager
