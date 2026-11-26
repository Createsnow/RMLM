<template>
  <!-- 电脑版框架，布局参考 MTP：左侧深色侧栏（品牌、分组菜单、底部个人卡片），顶栏为收起按钮 + 页签 + 常用操作，页签切换保留页面内容 -->
  <el-container class="pc">
    <el-aside :width="collapsed ? '64px' : '220px'" class="side">
      <div class="brand" :class="{ mini: collapsed }">
        <span class="logo">R</span>
        <div v-show="!collapsed" class="brand-text">
          <div class="brand-title">研发物料管家</div>
          <div class="brand-sub">RMLM · 研发物料全生命周期</div>
        </div>
      </div>
      <div class="quick" :class="{ mini: collapsed }">
        <el-tooltip :disabled="!collapsed" content="新建申请" placement="right">
          <el-button type="primary" class="new-btn" @click="onMenu('/apply')">
            <el-icon><Plus /></el-icon><span v-show="!collapsed">新建申请</span>
          </el-button>
        </el-tooltip>
      </div>
      <el-scrollbar class="menu-wrap">
        <el-menu :default-active="activeMenu" :default-openeds="openeds" :collapse="collapsed" :collapse-transition="false"
          class="menu" @select="onMenu" @open="k => toggleGroup(k, true)" @close="k => toggleGroup(k, false)">
          <el-sub-menu v-for="g in menu" :key="g.title" :index="g.title" popper-class="rmlm-nav-pop">
            <template #title>
              <el-icon><component :is="g.icon" /></el-icon>
              <span class="gtitle">{{ g.title }}</span>
              <span v-if="g.badge && !isOpen(g.title)" class="gdot">{{ g.badge }}</span>
            </template>
            <el-menu-item v-for="m in g.items" :key="m.path" :index="m.path">
              <span class="item-title">{{ m.name }}</span>
              <span v-if="m.badge" class="ibadge">{{ m.badge > 99 ? '99+' : m.badge }}</span>
            </el-menu-item>
          </el-sub-menu>
        </el-menu>
      </el-scrollbar>
      <el-dropdown trigger="click" :placement="collapsed ? 'right-end' : 'top-start'" class="me-wrap" @command="onCommand">
        <div class="me" :class="{ mini: collapsed }" :title="collapsed ? `${store.user?.name || ''} · ${roleText}` : undefined">
          <span class="avatar">{{ initial }}</span>
          <template v-if="!collapsed">
            <span class="me-text">
              <span class="me-name">{{ store.user?.name }}</span>
              <span class="me-role">{{ roleText }}</span>
            </span>
            <el-icon class="me-caret"><MoreFilled /></el-icon>
          </template>
        </div>
        <template #dropdown>
          <div class="me-card">
            <span class="avatar lg">{{ initial }}</span>
            <div class="card-main">
              <div class="card-name">{{ store.user?.name }}</div>
              <div class="card-line">账号 {{ store.user?.username }}</div>
              <div class="card-tags">
                <el-tag v-for="r in store.user?.role_names || []" :key="r" size="small" effect="plain">{{ r }}</el-tag>
                <el-tag v-if="store.user?.frozen" size="small" type="danger">新领用已冻结</el-tag>
              </div>
            </div>
          </div>
          <el-dropdown-menu>
            <el-dropdown-item command="password" divided :icon="Lock">修改密码</el-dropdown-item>
            <el-dropdown-item command="delegates" :icon="Switch">代理审核设置</el-dropdown-item>
            <el-dropdown-item command="mobile" :icon="Iphone">切换到手机版</el-dropdown-item>
            <el-dropdown-item command="logout" divided :icon="SwitchButton">退出登录</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </el-aside>
    <el-container direction="vertical" class="body">
      <el-header class="top">
        <el-button text class="fold" :title="collapsed ? '展开菜单' : '收起菜单'" @click="setCollapsed(!collapsed)">
          <el-icon :size="18"><component :is="collapsed ? 'Expand' : 'Fold'" /></el-icon>
        </el-button>
        <TabBar :group-of="groupOf" />
        <div class="right">
          <el-tag v-if="store.user?.frozen" type="danger" effect="dark" size="small">新领用已冻结</el-tag>
          <el-tooltip content="扫码">
            <el-button text circle class="ibtn" @click="onMenu('/scan')"><el-icon :size="18"><Camera /></el-icon></el-button>
          </el-tooltip>
          <el-tooltip content="我的待办">
            <el-badge :value="todoCount" :hidden="!todoCount" :max="99" class="bell">
              <el-button text circle class="ibtn" @click="onMenu('/todos')"><el-icon :size="18"><Bell /></el-icon></el-button>
            </el-badge>
          </el-tooltip>
        </div>
      </el-header>
      <el-main class="main">
        <!-- 页签缓存：切换页签不重新加载，页面内容保留；关闭或刷新页签时释放（见 pageOf） -->
        <router-view v-slot="{ Component, route: r }">
          <transition name="page" mode="out-in">
            <keep-alive :include="cached">
              <component :is="pageOf(Component, tabKeyOf(r))" :key="pageName(tabKeyOf(r))" />
            </keep-alive>
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed, defineComponent, h, markRaw, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Iphone, Lock, Switch, SwitchButton } from '@element-plus/icons-vue'
import { api } from '../api'
import { store, hasRole, setUser, logout } from '../store'
import { loadDicts } from '../utils'
import { HOME } from '../tabList'
import { tabs, findTab, visitTab, restoreTabs, resetTabs, tabKeyOf } from '../tabs'
import TabBar from './TabBar.vue'

const route = useRoute()
const router = useRouter()
const todoCount = computed(() => store.counts.tasks + store.counts.notices)

// 本机偏好（收起菜单、各分组展开状态）：读写失败时按默认显示
const PREF_KEY = 'rmlm_pc_nav'
function readPref() { try { return JSON.parse(localStorage.getItem(PREF_KEY) || '{}') || {} } catch { return {} } }
function savePref() { try { localStorage.setItem(PREF_KEY, JSON.stringify({ collapsed: collapsed.value, groups: groupState.value })) } catch { /* ignore */ } }
const pref = readPref()
const collapsed = ref(!!pref.collapsed)
const groupState = ref(pref.groups || {})
function setCollapsed(v) { collapsed.value = v; savePref() }
function toggleGroup(k, open) { groupState.value = { ...groupState.value, [k]: open }; savePref() }

const menu = computed(() => {
  const u = store.user || { roles: [] }
  const mgr = u.is_manager || (u.delegated_by || []).length
  const groups = [
    { title: '工作台', icon: 'Monitor', items: [
      { path: '/', name: '总览看板' },
      { path: '/todos', name: '我的待办', badge: todoCount.value },
      { path: '/scan', name: '扫码' }
    ] },
    { title: '领料', icon: 'Document', items: [
      { path: '/reqs', name: '领料申请' },
      ...(mgr ? [{ path: '/approve', name: '待我审核' }] : []),
      { path: '/ledger', name: '领用台账' },
      ...(u.is_manager ? [{ path: '/team', name: '本组人员' }] : [])
    ] }
  ]
  const ops = []
  if (hasRole('KEEPER')) ops.push({ path: '/issue', name: '开单绑定' })
  if (hasRole('KEEPER', 'FINANCE', 'WAREHOUSE', 'ADMIN')) ops.push({ path: '/erp', name: 'ERP 单据' })
  if (hasRole('KEEPER', 'WAREHOUSE', 'ADMIN')) ops.push({ path: '/dispose', name: '退库 / 报废' })
  if (hasRole('KEEPER', 'FINANCE', 'ADMIN', 'DEPT_HEAD', 'VP')) ops.push({ path: '/recon', name: '对账与关账' })
  if (hasRole('KEEPER', 'ADMIN', 'FINANCE')) ops.push({ path: '/reversals', name: '冲销' })
  if (ops.length) groups.push({ title: '物料作业', icon: 'Box', items: ops })
  const mg = [{ path: '/reports', name: '报表' }]
  if (hasRole('KEEPER', 'ADMIN', 'WAREHOUSE')) mg.push({ path: '/master', name: '主数据' })
  if (hasRole('KEEPER', 'ADMIN', 'FINANCE', 'DEPT_HEAD', 'VP', 'WAREHOUSE')) mg.push({ path: '/history', name: '历史基线' })
  mg.push({ path: '/dicts', name: '字典与规则' })
  if (hasRole('ADMIN')) mg.push({ path: '/users', name: '账号管理' }, { path: '/audit', name: '审计日志' })
  groups.push({ title: '管理', icon: 'Setting', items: mg })
  for (const g of groups) g.badge = g.items.reduce((n, m) => n + (m.badge || 0), 0)
  return groups
})

// 详情、新建等不在菜单里的页面，高亮其所属的菜单项
const MENU_OF = { '/req/:id': '/reqs', '/apply': '/reqs', '/line/:id': '/ledger' }
const menuOf = key => MENU_OF[key] || key
const activeMenu = computed(() => menuOf(tabKeyOf(route)))
const groupOf = key => menu.value.find(g => g.items.some(m => m.path === menuOf(key)))?.title || ''
const currentGroup = computed(() => groupOf(tabKeyOf(route)))
// 默认展开：工作台、领料、当前页所在分组；用户手动展开 / 收起过的分组按其选择
const isOpen = t => t in groupState.value ? groupState.value[t] : (t === '工作台' || t === '领料' || t === currentGroup.value)
const openeds = computed(() => menu.value.map(g => g.title).filter(isOpen))
const roleText = computed(() => {
  const r = store.user?.role_names || []
  return r.length > 1 ? `${r[0]} 等 ${r.length} 个角色` : (r[0] || '')
})
const initial = computed(() => (store.user?.name || '?').trim().slice(-2))

// ================================================================ 顶栏页签

/** F5 恢复页签时只留还存在的电脑版页面 */
restoreTabs(store.user?.id, t => {
  const r = router.resolve(t.fullPath)
  return r.matched[0]?.path === '/' && !!r.meta.title && tabKeyOf(r) === t.key
})
watch(() => route.fullPath, () => {
  // 离开电脑版框架（打印标签、手机版、登录页）时不记页签
  if (route.matched[0]?.path === '/') visitTab({ key: tabKeyOf(route), fullPath: route.fullPath })
}, { immediate: true })

/** 菜单：已打开的功能切回上次的地址（保留页面内的参数和内容），未打开的新开页签 */
function onMenu(path) {
  router.push(findTab(path)?.fullPath ?? path)
}

/**
 * 每个页签一个缓存实例：按「页签#版本」包一层具名组件交给 keep-alive。
 * 刷新、按新地址重开时版本变化，旧实例随 include 变化释放。
 */
const pageName = key => `${key}#${findTab(key)?.ver ?? 0}`
const wrappers = new Map()
function pageOf(vnode, key) {
  if (!vnode) return undefined
  const name = pageName(key)
  let w = wrappers.get(name)
  if (!w) {
    const type = vnode.type
    w = markRaw(defineComponent({ name, setup: () => () => h(type) }))
    wrappers.set(name, w)
  }
  return w
}
/** 首页、我的待办不缓存：每次切回都重新加载，数字和待办是最新的 */
const NO_CACHE = [HOME, '/todos']
const cached = ref([])
watch([() => tabs.list.map(x => pageName(x.key)).join('|'), () => route.path], () => {
  const names = tabs.list.map(x => pageName(x.key))
  cached.value = tabs.list.filter(x => !NO_CACHE.includes(x.key)).map(x => pageName(x.key))
  for (const name of wrappers.keys()) if (!names.includes(name)) wrappers.delete(name)
}, { immediate: true, flush: 'post' })

// ================================================================ 个人菜单

function toMobile() { sessionStorage.removeItem('rmlm_pc'); router.push('/m') }
function onCommand(cmd) {
  if (cmd === 'password') onMenu('/password')
  else if (cmd === 'delegates') onMenu('/delegates')
  else if (cmd === 'mobile') toMobile()
  else if (cmd === 'logout') { resetTabs(); logout() }
}

let timer
async function refresh() {
  try {
    const me = await api.get('/api/auth/me', { silent: true })
    setUser(me)
    store.counts = await api.get('/api/todos/count', { silent: true })
  } catch { /* 401 已跳登录 */ }
}
// 窗口变窄时自动收起侧栏（不改本机偏好）
function onResize() { if (window.innerWidth < 1100) collapsed.value = true }
onMounted(() => {
  sessionStorage.setItem('rmlm_pc', '1')
  loadDicts(); refresh(); timer = setInterval(refresh, 60000)
  onResize(); window.addEventListener('resize', onResize)
})
onUnmounted(() => { clearInterval(timer); window.removeEventListener('resize', onResize) })
</script>

<style scoped>
.pc { height: 100vh; overflow: hidden; }
.side { background: #0f2741; color: #cfd8e3; display: flex; flex-direction: column; transition: width .18s ease; overflow: hidden; }
.brand { display: flex; align-items: center; gap: 10px; padding: 14px 16px; color: #fff; flex-shrink: 0; white-space: nowrap; }
.brand.mini { padding: 14px 0; justify-content: center; }
.logo { width: 30px; height: 30px; flex-shrink: 0; border-radius: 8px; background: linear-gradient(135deg, #1f8fff, #0b5cb8); display: grid; place-items: center; font-weight: 700; }
.brand-title { font-weight: 700; font-size: 15px; }
.brand-sub { font-size: 11px; color: #7f93ab; margin-top: 2px; }
.quick { padding: 2px 14px 10px; flex-shrink: 0; }
.quick.mini { padding: 2px 10px 10px; }
.new-btn { width: 100%; height: 36px; --el-button-bg-color: #1f8fff; --el-button-border-color: #1f8fff; }
.new-btn .el-icon + span { margin-left: 6px; }
.menu-wrap { flex: 1; min-height: 0; }
.menu { border-right: none; background: transparent;
  --el-menu-bg-color: transparent; --el-menu-text-color: #cfd8e3; --el-menu-hover-bg-color: #18395c;
  --el-menu-active-color: #fff; --el-menu-item-height: 44px; }
.menu :deep(.el-sub-menu__title) { height: 44px; line-height: 44px; }
.menu :deep(.el-sub-menu .el-menu-item) { height: 40px; line-height: 40px; padding-left: 52px !important; font-size: 13.5px; }
.menu :deep(.el-menu-item.is-active) { background: #1f5fa8; }
.menu :deep(.el-sub-menu.is-active > .el-sub-menu__title) { color: #fff; }
.gtitle { flex: 1; }
.gdot { background: #f56c6c; color: #fff; font-size: 11px; line-height: 16px; padding: 0 6px; border-radius: 8px; margin-right: 18px; }
.item-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.ibadge { flex: none; margin-left: 6px; background: #f56c6c; color: #fff; font-size: 11px; line-height: 18px; min-width: 18px; text-align: center; padding: 0 5px; border-radius: 9px; }

.me-wrap { display: block; flex: none; border-top: 1px solid #1b3a5c; }
.me { display: flex; align-items: center; gap: 10px; padding: 12px 14px; cursor: pointer; color: #e2e8f0; transition: background .15s; outline: none; }
.me:hover { background: #18395c; }
.me.mini { justify-content: center; padding: 12px 0; }
.avatar { flex: none; width: 32px; height: 32px; border-radius: 50%; background: #1f8fff; color: #fff; font-size: 12.5px; font-weight: 700; display: grid; place-items: center; }
.avatar.lg { width: 40px; height: 40px; font-size: 14px; }
.me-text { display: flex; flex-direction: column; line-height: 1.25; min-width: 0; flex: 1; }
.me-name { font-size: 13px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.me-role { font-size: 11px; color: #7f93ab; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.me-caret { color: #5f7690; flex: none; }
.me-card { display: flex; gap: 12px; padding: 14px 16px 10px; min-width: 240px; max-width: 320px; }
.card-main { min-width: 0; }
.card-name { font-weight: 600; font-size: 14px; color: var(--ink); }
.card-line { font-size: 12px; color: var(--ink2); margin-top: 2px; }
.card-tags { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }

.body { min-width: 0; }
.top { height: 48px; background: #fff; border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 8px; padding: 0 16px 0 8px; }
.fold { flex: none; width: 32px; height: 32px; padding: 0; color: var(--ink2); }
.right { display: flex; align-items: center; gap: 4px; flex: none; }
.ibtn { width: 34px; height: 34px; color: var(--ink2); }
.bell :deep(.el-badge__content) { top: 6px; right: 10px; }
.main { background: var(--bg); padding: 20px; overflow: auto; }
</style>

<style>
/* 收起菜单时分组弹出的子菜单（挂在 body 下，不能用 scoped）：与侧栏同色 */
.rmlm-nav-pop .el-menu { background: #0f2741; --el-menu-bg-color: #0f2741; --el-menu-text-color: #cfd8e3;
  --el-menu-hover-bg-color: #18395c; --el-menu-active-color: #fff; --el-menu-item-height: 38px; padding: 4px 0; min-width: 150px; }
.rmlm-nav-pop .el-menu-item { font-size: 13.5px; }
.rmlm-nav-pop .el-menu-item.is-active { background: #1f5fa8; }
.rmlm-nav-pop .ibadge { margin-left: 8px; background: #f56c6c; color: #fff; font-size: 11px; line-height: 18px; padding: 0 5px; border-radius: 9px; }
/* 页面切换过渡 */
.page-enter-active, .page-leave-active { transition: opacity .15s ease, transform .15s ease; }
.page-enter-from { opacity: 0; transform: translateY(6px); }
.page-leave-to { opacity: 0; transform: translateY(-4px); }
</style>
