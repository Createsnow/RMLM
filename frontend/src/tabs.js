import { reactive, watch } from 'vue'
import * as list from './tabList'

/** 电脑版顶栏页签。保存在本浏览器标签页内（F5 刷新后恢复，关掉浏览器标签页即清空），按账户区分。纯逻辑见 tabList.js。 */
const KEY = 'rmlm_tabs'
let uid = null

export const tabs = reactive({ list: list.withHome([]) })

watch(() => tabs.list, v => {
  if (uid == null) return
  try { sessionStorage.setItem(KEY, JSON.stringify({ uid, tabs: v })) } catch { /* 存不了只是刷新后不恢复 */ }
})

function readSaved() {
  try { return JSON.parse(sessionStorage.getItem(KEY) || 'null') } catch { return null }
}

/** 进入电脑版框架时读回本账户保存的页签。 */
export function restoreTabs(userId, allowed) {
  uid = userId ?? null
  tabs.list = list.restoreTabs(readSaved(), uid, allowed)
}

/** 退出登录：清空页签。 */
export function resetTabs() {
  uid = null
  tabs.list = list.withHome([])
  try { sessionStorage.removeItem(KEY) } catch { /* ignore */ }
}

export const findTab = key => tabs.list.find(t => t.key === key)
export const visitTab = to => { tabs.list = list.visit(tabs.list, to) }
/** 关闭一个页签，返回需要切换到的页签（关的不是当前页时为 null）。 */
export function closeTab(key, activeKey) {
  const r = list.closeTab(tabs.list, key, activeKey)
  tabs.list = r.tabs
  return r.next
}
export const closeOthers = key => { tabs.list = list.closeOthers(tabs.list, key) }
export const closeRight = key => { tabs.list = list.closeRight(tabs.list, key) }
export const closeAll = () => { tabs.list = list.closeAll(tabs.list) }
export const refreshTab = key => { tabs.list = list.refreshTab(tabs.list, key) }
export const moveTab = (from, to) => { tabs.list = list.moveTab(tabs.list, from, to) }

/** 页签标识：路由记录的路径（/req/:id 这类详情页共用一个页签）。 */
export const tabKeyOf = route => route.matched[route.matched.length - 1]?.path || route.path
