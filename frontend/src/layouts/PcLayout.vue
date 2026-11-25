<template>
  <el-container class="pc">
    <el-aside width="208px" class="side">
      <div class="brand"><span class="logo">R</span>研发物料管家</div>
      <el-menu :default-active="active" router class="menu">
        <template v-for="g in menu" :key="g.title">
          <div class="group">{{ g.title }}</div>
          <el-menu-item v-for="m in g.items" :key="m.path" :index="m.path">
            <el-icon><component :is="m.icon" /></el-icon>
            <span>{{ m.name }}</span>
            <el-badge v-if="m.badge" :value="m.badge" class="mbadge" />
          </el-menu-item>
        </template>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="top">
        <div class="title">{{ $route.meta.title }}</div>
        <div class="grow" />
        <el-tag v-if="store.user?.frozen" type="danger" effect="dark">新领用已冻结</el-tag>
        <span class="who">{{ store.user?.name }} <span class="muted">{{ (store.user?.role_names || []).join(' / ') }}</span></span>
        <el-button text @click="toMobile"><el-icon><Iphone /></el-icon>手机版</el-button>
        <el-dropdown>
          <el-button text><el-icon><Setting /></el-icon></el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item @click="$router.push('/password')">修改密码</el-dropdown-item>
              <el-dropdown-item @click="$router.push('/delegates')">代理审核设置</el-dropdown-item>
              <el-dropdown-item divided @click="logout">退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </el-header>
      <el-main class="main"><router-view :key="$route.fullPath" /></el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { api } from '../api'
import { store, hasRole, setUser, logout } from '../store'
import { loadDicts } from '../utils'

const route = useRoute()
const router = useRouter()
const active = computed(() => '/' + (route.path.split('/')[1] || ''))

const menu = computed(() => {
  const u = store.user || { roles: [] }
  const mgr = u.is_manager || (u.delegated_by || []).length
  const groups = [
    { title: '工作台', items: [
      { path: '/', name: '总览看板', icon: 'DataBoard' },
      { path: '/todos', name: '我的待办', icon: 'Bell', badge: store.counts.tasks + store.counts.notices },
      { path: '/scan', name: '扫码', icon: 'Camera' }
    ] },
    { title: '领料', items: [
      { path: '/apply', name: '新建申请', icon: 'EditPen' },
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
  if (ops.length) groups.push({ title: '物料作业', items: ops })
  const mg = [{ path: '/reports', name: '报表', icon: 'TrendCharts' }]
  if (hasRole('KEEPER', 'ADMIN', 'WAREHOUSE')) mg.push({ path: '/master', name: '主数据', icon: 'Box' })
  if (hasRole('KEEPER', 'ADMIN', 'FINANCE', 'DEPT_HEAD', 'VP', 'WAREHOUSE')) mg.push({ path: '/history', name: '历史基线', icon: 'Clock' })
  mg.push({ path: '/dicts', name: '字典与规则', icon: 'Collection' })
  if (hasRole('ADMIN')) mg.push({ path: '/users', name: '账号管理', icon: 'UserFilled' }, { path: '/audit', name: '审计日志', icon: 'View' })
  groups.push({ title: '管理', items: mg })
  return groups
})

let timer
async function refresh() {
  try {
    const me = await api.get('/api/auth/me', { silent: true })
    setUser(me)
    store.counts = await api.get('/api/todos/count', { silent: true })
    if (me.must_change_pw && route.path !== '/password') {
      ElMessageBox.alert('首次登录或密码已被重置，请先修改密码。', '提示').then(() => router.push('/password'))
    }
  } catch { /* 401 已跳登录 */ }
}
function toMobile() { sessionStorage.removeItem('rmlm_pc'); router.push('/m') }
onMounted(() => { sessionStorage.setItem('rmlm_pc', '1'); loadDicts(); refresh(); timer = setInterval(refresh, 60000) })
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.pc { height: 100vh; }
.side { background: #0f2741; color: #cfd8e3; overflow-y: auto; }
.brand { display: flex; align-items: center; gap: 8px; padding: 16px; font-weight: 700; color: #fff; font-size: 15px; }
.logo { width: 28px; height: 28px; border-radius: 7px; background: linear-gradient(135deg, #1f8fff, #0b5cb8); display: grid; place-items: center; }
.group { font-size: 11px; letter-spacing: 2px; color: #7f93ab; padding: 14px 20px 4px; }
.menu { border-right: none; background: transparent; --el-menu-text-color: #cfd8e3; --el-menu-hover-bg-color: #18395c; --el-menu-active-color: #fff; --el-menu-item-height: 42px; }
.menu :deep(.el-menu-item.is-active) { background: #1f5fa8; }
.mbadge { margin-left: auto; }
.top { display: flex; align-items: center; gap: 12px; background: #fff; border-bottom: 1px solid var(--line); }
.title { font-weight: 700; font-size: 16px; }
.grow { flex: 1; }
.who { font-size: 13px; }
.main { background: var(--bg); }
</style>
