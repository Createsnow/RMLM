<template>
  <el-container class="pc">
    <el-aside :width="collapsed ? '64px' : '216px'" class="side">
      <div class="brand" :class="{ mini: collapsed }">
        <span class="logo">R</span><span v-show="!collapsed" class="brand-name">研发物料管家</span>
      </div>
      <div class="quick" :class="{ mini: collapsed }">
        <el-tooltip :disabled="!collapsed" content="新建申请" placement="right">
          <el-button type="primary" class="new-btn" @click="$router.push('/apply')">
            <el-icon><Plus /></el-icon><span v-show="!collapsed">新建申请</span>
          </el-button>
        </el-tooltip>
      </div>
      <el-scrollbar class="menu-wrap">
        <el-menu :default-active="active" :default-openeds="openeds" :collapse="collapsed" :collapse-transition="false"
          router class="menu" @open="k => toggleGroup(k, true)" @close="k => toggleGroup(k, false)">
          <el-sub-menu v-for="g in menu" :key="g.title" :index="g.title" popper-class="rmlm-nav-pop">
            <template #title>
              <el-icon><component :is="g.icon" /></el-icon>
              <span class="gtitle">{{ g.title }}</span>
              <span v-if="g.badge && !isOpen(g.title)" class="gdot">{{ g.badge }}</span>
            </template>
            <el-menu-item v-for="m in g.items" :key="m.path" :index="m.path">
              <el-icon><component :is="m.icon" /></el-icon>
              <span>{{ m.name }}</span>
              <span v-if="m.badge" class="ibadge">{{ m.badge > 99 ? '99+' : m.badge }}</span>
            </el-menu-item>
          </el-sub-menu>
        </el-menu>
      </el-scrollbar>
      <div class="fold" @click="setCollapsed(!collapsed)">
        <el-icon><component :is="collapsed ? 'Expand' : 'Fold'" /></el-icon><span v-show="!collapsed">收起菜单</span>
      </div>
    </el-aside>
    <el-container>
      <el-header class="top">
        <el-breadcrumb separator="/" class="crumb">
          <el-breadcrumb-item v-if="currentGroup">{{ currentGroup }}</el-breadcrumb-item>
          <el-breadcrumb-item>{{ $route.meta.title }}</el-breadcrumb-item>
        </el-breadcrumb>
        <div class="grow" />
        <el-tag v-if="store.user?.frozen" type="danger" effect="dark">新领用已冻结</el-tag>
        <el-tooltip content="扫码">
          <el-button text circle class="ibtn" @click="$router.push('/scan')"><el-icon :size="18"><Camera /></el-icon></el-button>
        </el-tooltip>
        <el-tooltip content="我的待办">
          <el-badge :value="todoCount" :hidden="!todoCount" :max="99" class="bell">
            <el-button text circle class="ibtn" @click="$router.push('/todos')"><el-icon :size="18"><Bell /></el-icon></el-button>
          </el-badge>
        </el-tooltip>
        <el-dropdown trigger="click" placement="bottom-end">
          <div class="me">
            <span class="avatar">{{ (store.user?.name || '?').slice(-2) }}</span>
            <div class="me-text">
              <div class="me-name">{{ store.user?.name }}</div>
              <div class="me-role">{{ roleText }}</div>
            </div>
            <el-icon class="caret"><ArrowDown /></el-icon>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <div class="me-card">
                <b>{{ store.user?.name }}</b> <span class="muted">{{ store.user?.username }}</span>
                <div class="muted">{{ (store.user?.role_names || []).join(' / ') || '—' }}</div>
              </div>
              <el-dropdown-item divided :icon="Lock" @click="$router.push('/password')">修改密码</el-dropdown-item>
              <el-dropdown-item :icon="Switch" @click="$router.push('/delegates')">代理审核设置</el-dropdown-item>
              <el-dropdown-item :icon="Iphone" @click="toMobile">切换到手机版</el-dropdown-item>
              <el-dropdown-item divided :icon="SwitchButton" @click="logout">退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </el-header>
      <el-main class="main"><router-view :key="$route.fullPath" /></el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Iphone, Lock, Switch, SwitchButton } from '@element-plus/icons-vue'
import { api } from '../api'
import { store, hasRole, setUser, logout } from '../store'
import { loadDicts } from '../utils'

const route = useRoute()
const router = useRouter()
const active = computed(() => '/' + (route.path.split('/')[1] || ''))
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
      { path: '/', name: '总览看板', icon: 'DataBoard' },
      { path: '/todos', name: '我的待办', icon: 'Bell', badge: todoCount.value },
      { path: '/scan', name: '扫码', icon: 'Camera' }
    ] },
    { title: '领料', icon: 'Document', items: [
      { path: '/reqs', name: '领料申请', icon: 'Document' },
      ...(mgr ? [{ path: '/approve', name: '待我审核', icon: 'Stamp' }] : []),
      { path: '/ledger', name: '领用台账', icon: 'Tickets' },
      ...(u.is_manager ? [{ path: '/team', name: '本组人员', icon: 'User' }] : [])
    ] }
  ]
  const ops = []
  if (hasRole('KEEPER')) ops.push({ path: '/issue', name: '开单绑定', icon: 'Link' })
  if (hasRole('KEEPER', 'FINANCE', 'WAREHOUSE', 'ADMIN')) ops.push({ path: '/erp', name: 'ERP 单据', icon: 'Files' })
  if (hasRole('KEEPER', 'WAREHOUSE', 'ADMIN')) ops.push({ path: '/dispose', name: '退库 / 报废', icon: 'Back' })
  if (hasRole('KEEPER', 'FINANCE', 'ADMIN', 'DEPT_HEAD', 'VP')) ops.push({ path: '/recon', name: '对账与关账', icon: 'Money' })
  if (hasRole('KEEPER', 'ADMIN', 'FINANCE')) ops.push({ path: '/reversals', name: '冲销', icon: 'RefreshLeft' })
  if (ops.length) groups.push({ title: '物料作业', icon: 'Box', items: ops })
  const mg = [{ path: '/reports', name: '报表', icon: 'TrendCharts' }]
  if (hasRole('KEEPER', 'ADMIN', 'WAREHOUSE')) mg.push({ path: '/master', name: '主数据', icon: 'Coin' })
  if (hasRole('KEEPER', 'ADMIN', 'FINANCE', 'DEPT_HEAD', 'VP', 'WAREHOUSE')) mg.push({ path: '/history', name: '历史基线', icon: 'Clock' })
  mg.push({ path: '/dicts', name: '字典与规则', icon: 'Collection' })
  if (hasRole('ADMIN')) mg.push({ path: '/users', name: '账号管理', icon: 'UserFilled' }, { path: '/audit', name: '审计日志', icon: 'View' })
  groups.push({ title: '管理', icon: 'Setting', items: mg })
  for (const g of groups) g.badge = g.items.reduce((n, m) => n + (m.badge || 0), 0)
  return groups
})

const currentGroup = computed(() => menu.value.find(g => g.items.some(m => m.path === active.value))?.title || '')
// 默认展开：工作台、领料、当前页所在分组；用户手动展开 / 收起过的分组按其选择
const isOpen = t => t in groupState.value ? groupState.value[t] : (t === '工作台' || t === '领料' || t === currentGroup.value)
const openeds = computed(() => menu.value.map(g => g.title).filter(isOpen))
const roleText = computed(() => {
  const r = store.user?.role_names || []
  return r.length > 1 ? `${r[0]} 等 ${r.length} 个角色` : (r[0] || '')
})

let timer
async function refresh() {
  try {
    const me = await api.get('/api/auth/me', { silent: true })
    setUser(me)
    store.counts = await api.get('/api/todos/count', { silent: true })
  } catch { /* 401 已跳登录 */ }
}
function toMobile() { sessionStorage.removeItem('rmlm_pc'); router.push('/m') }
onMounted(() => { sessionStorage.setItem('rmlm_pc', '1'); loadDicts(); refresh(); timer = setInterval(refresh, 60000) })
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.pc { height: 100vh; }
.side { background: #0f2741; color: #cfd8e3; display: flex; flex-direction: column; transition: width .2s; overflow: hidden; }
.brand { display: flex; align-items: center; gap: 10px; height: 56px; padding: 0 18px; font-weight: 700; color: #fff; font-size: 15px; flex-shrink: 0; white-space: nowrap; }
.brand.mini { padding: 0; justify-content: center; }
.logo { width: 28px; height: 28px; flex-shrink: 0; border-radius: 7px; background: linear-gradient(135deg, #1f8fff, #0b5cb8); display: grid; place-items: center; }
.quick { padding: 4px 14px 10px; flex-shrink: 0; }
.quick.mini { padding: 4px 10px 10px; }
.new-btn { width: 100%; height: 36px; --el-button-bg-color: #1f8fff; --el-button-border-color: #1f8fff; }
.new-btn .el-icon + span { margin-left: 6px; }
.menu-wrap { flex: 1; min-height: 0; }
.menu { border-right: none; background: transparent;
  --el-menu-bg-color: transparent; --el-menu-text-color: #cfd8e3; --el-menu-hover-bg-color: #18395c;
  --el-menu-active-color: #fff; --el-menu-item-height: 40px; --el-menu-sub-item-height: 38px; --el-menu-base-level-padding: 18px; --el-menu-level-padding: 14px; }
.menu:not(.el-menu--collapse) { width: 216px; }
.menu :deep(.el-sub-menu__title) { color: #7f93ab; font-size: 12.5px; letter-spacing: 1px; }
.menu :deep(.el-sub-menu__title:hover) { color: #cfd8e3; }
.menu :deep(.el-menu-item) { margin: 1px 8px; border-radius: 6px; font-size: 13.5px; }
.menu :deep(.el-menu-item.is-active) { background: #1f5fa8; }
.menu.el-menu--collapse :deep(.el-menu-item) { margin: 1px 0; }
.gtitle { flex: 1; }
.gdot { background: #f56c6c; color: #fff; font-size: 11px; line-height: 16px; padding: 0 6px; border-radius: 8px; margin-right: 18px; letter-spacing: 0; }
.ibadge { margin-left: auto; background: #f56c6c; color: #fff; font-size: 11px; line-height: 18px; min-width: 18px; text-align: center; padding: 0 5px; border-radius: 9px; }
.fold { display: flex; align-items: center; justify-content: center; gap: 6px; height: 44px; flex-shrink: 0; border-top: 1px solid #1b3a5c; color: #7f93ab; font-size: 12.5px; cursor: pointer; white-space: nowrap; }
.fold:hover { color: #fff; background: #18395c; }

.top { display: flex; align-items: center; gap: 6px; height: 56px; background: #fff; border-bottom: 1px solid var(--line); }
.crumb { font-size: 14px; }
.crumb :deep(.el-breadcrumb__item:last-child .el-breadcrumb__inner) { font-weight: 700; color: var(--ink); }
.grow { flex: 1; }
.ibtn { width: 36px; height: 36px; color: var(--ink2); }
.bell :deep(.el-badge__content) { top: 6px; right: 10px; }
.me { display: flex; align-items: center; gap: 8px; padding: 4px 8px 4px 4px; margin-left: 6px; border-radius: 20px; cursor: pointer; outline: none; }
.me:hover { background: #f1f4f8; }
.avatar { width: 32px; height: 32px; border-radius: 50%; background: #e6f0fb; color: var(--brand); font-size: 12.5px; font-weight: 700; display: grid; place-items: center; }
.me-text { line-height: 1.2; }
.me-name { font-size: 13.5px; font-weight: 600; }
.me-role { font-size: 11.5px; color: var(--ink2); }
.caret { color: var(--ink2); }
.me-card { padding: 6px 16px 8px; font-size: 13px; line-height: 1.6; min-width: 200px; }
.main { background: var(--bg); }
</style>

<style>
/* 收起菜单时分组弹出的子菜单（挂在 body 下，不能用 scoped） */
.rmlm-nav-pop .el-menu { --el-menu-item-height: 38px; padding: 4px 0; min-width: 150px; }
.rmlm-nav-pop .el-menu-item { font-size: 13.5px; margin: 0 4px; border-radius: 6px; }
</style>
