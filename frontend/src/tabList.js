/**
 * 电脑版顶栏页签的纯逻辑（不依赖 Vue）。参考 MTP 的页签做法。
 * 页签 = { key, fullPath, ver }：key 是路由记录的路径（如 /ledger、/req/:id，一个功能一个页签，详情页共用一个），
 * fullPath 是带参数的完整地址（切回时原样恢复），ver 是页面实例版本：新开、刷新、按新地址重新打开时取新值，
 * 旧实例的缓存随之释放。首页固定在最左，不能关闭、不能拖动。
 * @typedef {{ key: string, fullPath: string, ver: number }} Tab
 */

export const HOME = '/'

/** @param {Tab[]} tabs */
export const maxVer = tabs => tabs.reduce((m, t) => Math.max(m, t.ver), 0)

/** 首页放在第一个；没有就补上。 @param {Tab[]} tabs @returns {Tab[]} */
export function withHome(tabs) {
  const home = tabs.find(t => t.key === HOME) ?? { key: HOME, fullPath: HOME, ver: maxVer(tabs) + 1 }
  return [home, ...tabs.filter(t => t.key !== HOME)]
}

/**
 * 进入某页（菜单、页签、页面内跳转、浏览器前进后退都走这里）。
 * 没打开过：追加在末尾。已打开且地址相同：沿用原页面（保留填了一半的内容）。
 * 已打开但地址不同（换了一张单据、带了新参数）：换新版本，页面按新地址重新打开——各页面只在打开时读取地址参数。
 * @param {Tab[]} tabs @param {{ key: string, fullPath: string }} to @returns {Tab[]}
 */
export function visit(tabs, to) {
  const i = tabs.findIndex(t => t.key === to.key)
  if (i < 0) return [...tabs, { key: to.key, fullPath: to.fullPath, ver: maxVer(tabs) + 1 }]
  if (tabs[i].fullPath === to.fullPath) return tabs
  const next = { ...tabs[i], fullPath: to.fullPath, ver: maxVer(tabs) + 1 }
  return tabs.map((t, k) => (k === i ? next : t))
}

/**
 * 关闭一个页签。关的是当前页时，激活右边的；右边没有就激活左边的（与浏览器一致）。
 * @returns {{ tabs: Tab[], next: Tab | null }} next = 需要切换到的页签
 */
export function closeTab(tabs, key, activeKey) {
  const i = tabs.findIndex(t => t.key === key)
  if (key === HOME || i < 0) return { tabs, next: null }
  const rest = tabs.filter((_, k) => k !== i)
  const next = key === activeKey ? (rest[i] ?? rest[i - 1] ?? null) : null
  return { tabs: rest, next }
}

/** 关闭其他：只留首页和该页签。 */
export const closeOthers = (tabs, key) => tabs.filter(t => t.key === HOME || t.key === key)

/** 关闭右侧。 */
export function closeRight(tabs, key) {
  const i = tabs.findIndex(t => t.key === key)
  return i < 0 ? tabs : tabs.slice(0, i + 1)
}

/** 关闭全部：只留首页。 */
export const closeAll = tabs => tabs.filter(t => t.key === HOME)

/** 刷新：换新版本，页面重新加载。 */
export function refreshTab(tabs, key) {
  const ver = maxVer(tabs) + 1
  return tabs.map(t => (t.key === key ? { ...t, ver } : t))
}

/** 拖动排序：首页不动，也不能把别的页签放到首页前面。 */
export function moveTab(tabs, from, to) {
  if (from === to || from <= 0 || to <= 0 || from >= tabs.length || to >= tabs.length) return tabs
  const out = [...tabs]
  const [t] = out.splice(from, 1)
  out.splice(to, 0, t)
  return out
}

/**
 * 读回 F5 前保存的页签：只认同一账户保存的；去掉格式不对、重复、已不存在的页签。
 * @param {unknown} saved @param {number | string | null | undefined} uid @param {(t: Tab) => boolean} allowed
 */
export function restoreTabs(saved, uid, allowed) {
  const s = saved ?? {}
  if (uid == null || s.uid !== uid || !Array.isArray(s.tabs)) return withHome([])
  const seen = new Set()
  const tabs = s.tabs.filter(t => {
    const ok = t && typeof t.key === 'string' && typeof t.fullPath === 'string' && Number.isFinite(t.ver)
      && !seen.has(t.key) && (t.key === HOME || allowed(t))
    if (ok) seen.add(t.key)
    return ok
  }).map(t => ({ key: t.key, fullPath: t.fullPath, ver: t.ver }))
  return withHome(tabs)
}
