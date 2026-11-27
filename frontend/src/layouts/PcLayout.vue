<template>
  <!--
    电脑版框架，按 ERP 通行的三级布局：
    一级 顶栏 = 品牌 + 模块（工作台、领用管理…）+ 全局工具（扫码查找、待办）+ 右上角当前时间
    二级 左侧栏 = 当前模块的功能，每项带一句话说明，可收起成图标栏（按钮在页签栏最左）；左下角为当前账号与个人菜单
    三级 页面内页签；打开过的页面在内容区上方以页签保留，切换不丢内容
  -->
  <div class="pc" :class="{ folded: collapsed }">
    <header class="hd">
      <a class="brand" href="/" title="回到首页" @click.prevent="onMenu('/')">
        <span class="logo">R</span>
        <span class="brand-title">研发物料管家</span>
      </a>
      <nav class="mods" aria-label="功能模块">
        <a v-for="m in modules" :key="m.key" class="mod" :class="{ on: m.key === sideModule?.key }" :href="m.items[0].path"
          :aria-current="m.key === sideModule?.key ? 'page' : undefined" @click.prevent="openModule(m)">
          <el-icon :size="16"><component :is="m.icon" /></el-icon>
          <span>{{ m.title }}</span>
          <i v-if="m.badge" class="mdot" :title="`${m.badge} 项待办`" />
        </a>
      </nav>
      <div class="tools">
        <el-input v-model="lookup" class="lookup" placeholder="扫码或输入 物料码 / 申请号 / 单据号" clearable @keyup.enter="doLookup">
          <template #prefix><el-icon><Search /></el-icon></template>
          <template #suffix>
            <el-tooltip content="打开扫码页（摄像头 / 扫码枪）">
              <el-icon class="cam" @click.stop="onMenu('/scan')"><Camera /></el-icon>
            </el-tooltip>
          </template>
        </el-input>
        <el-tag v-if="store.user?.frozen" type="danger" effect="dark" size="small">新领用已冻结</el-tag>
        <el-tooltip content="我的待办">
          <el-badge :value="todoCount" :hidden="!todoCount" :max="99" class="bell">
            <button type="button" class="hbtn" aria-label="我的待办" @click="onMenu('/todos')"><el-icon :size="18"><Bell /></el-icon></button>
          </el-badge>
        </el-tooltip>
        <div class="clock" :title="`${clock.date} ${clock.time}`">
          <span class="ck-time">{{ clock.time }}</span>
          <span class="ck-date">{{ clock.date }}</span>
        </div>
      </div>
    </header>

    <div class="frame">
      <aside class="side" :style="{ width: collapsed ? '64px' : '216px' }">
        <div v-if="sideModule" class="side-head">
          <template v-if="!collapsed">
            <div class="side-title">{{ sideModule.title }}</div>
            <div class="side-desc">{{ sideModule.desc }}</div>
          </template>
          <el-tooltip v-else :content="sideModule.title" placement="right">
            <el-icon :size="18" class="side-icon"><component :is="sideModule.icon" /></el-icon>
          </el-tooltip>
        </div>
        <el-scrollbar class="side-scroll">
          <nav class="items" :aria-label="sideModule?.title">
            <el-tooltip v-for="it in sideModule?.items || []" :key="it.path" :disabled="!collapsed" placement="right"
              :content="it.badge ? `${it.name}（${it.badge}）` : it.name">
              <a class="item" :class="{ on: it.path === activeMenu }" :href="it.path" :aria-current="it.path === activeMenu ? 'page' : undefined"
                @click.prevent="onMenu(it.path)">
                <span class="ic"><el-icon :size="18"><component :is="it.icon" /></el-icon><i v-if="collapsed && it.badge" class="idot" /></span>
                <span v-if="!collapsed" class="txt">
                  <span class="nm">{{ it.name }}</span>
                  <span class="ds">{{ it.desc }}</span>
                </span>
                <span v-if="!collapsed && it.badge" class="badge">{{ it.badge > 99 ? '99+' : it.badge }}</span>
              </a>
            </el-tooltip>
          </nav>
        </el-scrollbar>
        <!-- 左下角：当前账号，点开为个人菜单 -->
        <el-dropdown trigger="click" :placement="collapsed ? 'right-end' : 'top-start'" class="me-wrap" @command="onCommand">
          <button type="button" class="me" :class="{ mini: collapsed }" :title="`${store.user?.name || ''} · ${roleText}`">
            <span class="avatar">{{ initial }}</span>
            <span v-if="!collapsed" class="me-text">
              <span class="me-name">{{ store.user?.name }}</span>
              <span class="me-role">{{ roleText }}</span>
            </span>
            <el-icon v-if="!collapsed" class="caret"><MoreFilled /></el-icon>
          </button>
          <template #dropdown>
            <div class="me-card">
              <span class="avatar lg">{{ initial }}</span>
              <div class="card-main">
                <div class="card-name">{{ store.user?.name }}</div>
                <div class="card-line">账号 {{ store.user?.username }}<template v-if="store.user?.manager_name"> · 直属上级 {{ store.user.manager_name }}</template></div>
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
      </aside>

      <section class="content">
        <div class="tabstrip">
          <el-tooltip :content="collapsed ? '展开菜单' : '收起菜单'">
            <button type="button" class="fold" :aria-label="collapsed ? '展开菜单' : '收起菜单'" @click="setCollapsed(!collapsed)">
              <el-icon :size="18"><component :is="collapsed ? 'Expand' : 'Fold'" /></el-icon>
            </button>
          </el-tooltip>
          <TabBar :group-of="groupOf" />
        </div>
        <main class="main">
          <!-- 页签缓存：切换页签不重新加载，页面内容保留；关闭或刷新页签时释放（见 pageOf） -->
          <router-view v-slot="{ Component, route: r }">
            <transition name="page" mode="out-in">
              <keep-alive :include="cached">
                <component :is="pageOf(Component, tabKeyOf(r))" :key="pageName(tabKeyOf(r))" />
              </keep-alive>
            </transition>
          </router-view>
        </main>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, defineComponent, h, markRaw, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Iphone, Lock, Switch, SwitchButton } from '@element-plus/icons-vue'
import { api } from '../api'
import { store, setUser, logout } from '../store'
import { loadDicts } from '../utils'
import { PERSONAL, menuOf, moduleOf, visibleModules } from '../nav'
import { tabs, findTab, visitTab, restoreTabs, resetTabs, tabKeyOf } from '../tabs'
import TabBar from './TabBar.vue'

const route = useRoute()
const router = useRouter()
const todoCount = computed(() => (store.counts.tasks || 0) + (store.counts.notices || 0))

// 本机偏好（收起菜单）：读写失败时按默认显示
const PREF_KEY = 'rmlm_pc_nav'
function readPref() { try { return JSON.parse(localStorage.getItem(PREF_KEY) || '{}') || {} } catch { return {} } }
const collapsed = ref(!!readPref().collapsed)
function setCollapsed(v) {
  collapsed.value = v
  try { localStorage.setItem(PREF_KEY, JSON.stringify({ collapsed: v })) } catch { /* ignore */ }
}

// ================================================================ 模块与功能

const modules = computed(() => visibleModules())
const currentKey = computed(() => tabKeyOf(route))
const activeMenu = computed(() => menuOf(currentKey.value))
/** 左侧栏显示的模块：当前页所属模块；个人设置等不属于模块的页面沿用上一个模块 */
const lastModuleKey = ref('work')
const routeModule = computed(() => moduleOf(currentKey.value, modules.value))
watch(routeModule, m => { if (m) lastModuleKey.value = m.key }, { immediate: true })
const sideModule = computed(() => routeModule.value
  || modules.value.find(m => m.key === lastModuleKey.value) || modules.value[0] || null)

/** 页签悬停提示「模块 / 页面」 */
const groupOf = key => moduleOf(key, modules.value)?.title || (PERSONAL[key] ? '个人设置' : '')

/** 各模块最近一次打开的页面：点模块时回到那里（页签已关闭则打开模块的第一个功能） */
const lastPathOf = {}
watch(() => route.fullPath, () => {
  const m = routeModule.value
  if (m && route.matched[0]?.path === '/') lastPathOf[m.key] = currentKey.value
})
function openModule(m) {
  const key = lastPathOf[m.key]
  onMenu(key && findTab(key) ? key : m.items[0].path)
}

const roleText = computed(() => {
  const r = store.user?.role_names || []
  return r.length > 1 ? `${r[0]} 等 ${r.length} 个角色` : (r[0] || '')
})
const initial = computed(() => (store.user?.name || '?').trim().slice(-2))

// ================================================================ 顶栏右上角时间（本机时间，每秒走）

const now = ref(new Date())
const pad = n => String(n).padStart(2, '0')
const clock = computed(() => {
  const d = now.value
  return {
    time: `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`,
    date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} 星期${'日一二三四五六'[d.getDay()]}`
  }
})

// ================================================================ 扫码查找

/** 顶栏查找：物料码 / 申请号直接打开详情；ERP 单据号、仓储条码、库位码到扫码页列出结果 */
const lookup = ref('')
async function doLookup() {
  const code = lookup.value.trim()
  if (!code) return onMenu('/scan')
  let r
  try { r = await api.get(`/api/scan/${encodeURIComponent(code)}`) } catch { return }
  lookup.value = ''
  if (r.kind === 'line') router.push(`/line/${r.line_id}`)
  else if (r.kind === 'req') router.push(`/req/${r.req_id}`)
  else router.push({ path: '/scan', query: { code } })
}

// ================================================================ 页签

/** F5 恢复页签时只留还存在的电脑版页面 */
restoreTabs(store.user?.id, t => {
  const r = router.resolve(t.fullPath)
  return r.matched[0]?.path === '/' && !!r.meta.title && tabKeyOf(r) === t.key
})
watch(() => route.fullPath, () => {
  // 离开电脑版框架（打印标签、手机版、登录页）时不记页签
  if (route.matched[0]?.path === '/') visitTab({ key: tabKeyOf(route), fullPath: route.fullPath })
}, { immediate: true })
// 切换页面时顺带更新待办数（侧栏、铃铛上的数字）
watch(() => route.path, () => refreshCounts())

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
/** 各页面切回时自行重新取数（见 useRefresh），页面实例本身都缓存，保留筛选条件和填了一半的内容 */
const cached = ref([])
watch([() => tabs.list.map(x => pageName(x.key)).join('|'), () => route.path], () => {
  const names = tabs.list.map(x => pageName(x.key))
  cached.value = names
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

let timer, clockTimer
async function refreshCounts() {
  try { store.counts = await api.get('/api/todos/count', { silent: true }) } catch { /* 401 已跳登录 */ }
}
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
  clockTimer = setInterval(() => { now.value = new Date() }, 1000)
  onResize(); window.addEventListener('resize', onResize)
})
onUnmounted(() => { clearInterval(timer); clearInterval(clockTimer); window.removeEventListener('resize', onResize) })
</script>

<style scoped>
.pc { height: 100vh; display: flex; flex-direction: column; overflow: hidden; background: var(--bg); }

/* ---------------- 一级：顶栏 */
.hd { flex: none; height: 56px; display: flex; align-items: center; gap: 8px; padding: 0 16px 0 0; background: #0f2741; color: #fff; }
.brand { flex: none; width: 216px; height: 100%; display: flex; align-items: center; gap: 10px; padding: 0 18px; color: #fff; }
.folded .brand { width: auto; }
.logo { width: 30px; height: 30px; flex: none; border-radius: 8px; background: linear-gradient(135deg, #1f8fff, #0b5cb8);
  display: grid; place-items: center; font-weight: 700; }
.brand-title { font-weight: 700; font-size: 16px; white-space: nowrap; letter-spacing: .5px; }
.mods { flex: 1; min-width: 0; height: 100%; display: flex; align-items: stretch; gap: 2px; overflow-x: auto; scrollbar-width: none; }
.mods::-webkit-scrollbar { display: none; }
.mod { position: relative; flex: none; display: flex; align-items: center; gap: 6px; padding: 0 14px; color: #b8c6d8; font-size: 14.5px;
  white-space: nowrap; transition: color .15s, background .15s; }
.mod:hover { color: #fff; background: rgba(255, 255, 255, .06); }
.mod.on { color: #fff; font-weight: 600; background: rgba(31, 143, 255, .16); }
.mod.on::after { content: ''; position: absolute; left: 12px; right: 12px; bottom: 0; height: 3px; border-radius: 3px 3px 0 0; background: #3b9bff; }
.mdot { position: absolute; top: 14px; right: 6px; width: 7px; height: 7px; border-radius: 50%; background: #f56c6c; }
.tools { flex: none; display: flex; align-items: center; gap: 8px; }
.lookup { width: 290px; }
.lookup :deep(.el-input__wrapper) { background: rgba(255, 255, 255, .1); box-shadow: none; border-radius: 16px; }
.lookup :deep(.el-input__wrapper.is-focus) { background: #fff; }
.lookup :deep(.el-input__inner) { color: #fff; }
.lookup :deep(.is-focus .el-input__inner) { color: var(--ink); }
.lookup :deep(.el-input__inner::placeholder) { color: #8ea2ba; }
.cam { cursor: pointer; color: #8ea2ba; }
.cam:hover { color: var(--brand); }
.hbtn { width: 36px; height: 36px; border: 0; border-radius: 8px; background: none; color: #cfd8e3; cursor: pointer; display: grid; place-items: center; }
.hbtn:hover { background: rgba(255, 255, 255, .1); color: #fff; }
.bell :deep(.el-badge__content) { top: 6px; right: 8px; border: 0; }
.clock { display: flex; flex-direction: column; align-items: flex-end; line-height: 1.2; padding-left: 10px; margin-left: 2px;
  border-left: 1px solid rgba(255, 255, 255, .14); white-space: nowrap; cursor: default; }
.ck-time { font-size: 16px; font-weight: 600; color: #fff; font-variant-numeric: tabular-nums; letter-spacing: .5px; }
.ck-date { font-size: 11.5px; color: #8ea2ba; margin-top: 2px; }

/* 左下角：当前账号 */
.me-wrap { display: block; flex: none; border-top: 1px solid var(--line); }
.me { display: flex; align-items: center; gap: 10px; width: 100%; padding: 10px 14px; border: 0; background: none; color: var(--ink);
  cursor: pointer; font: inherit; text-align: left; transition: background .12s; }
.me:hover { background: #f2f6fb; }
.me.mini { justify-content: center; padding: 10px 0; }
.avatar { flex: none; width: 32px; height: 32px; border-radius: 50%; background: #1f8fff; color: #fff; font-size: 12px; font-weight: 700; display: grid; place-items: center; }
.avatar.lg { width: 40px; height: 40px; font-size: 14px; }
.me-text { flex: 1; min-width: 0; display: flex; flex-direction: column; line-height: 1.3; }
.me-name { font-size: 13.5px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.me-role { font-size: 11.5px; color: var(--ink2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.caret { flex: none; color: #94a3b8; }
.me-card { display: flex; gap: 12px; padding: 14px 16px 10px; min-width: 260px; max-width: 340px; }
.card-main { min-width: 0; }
.card-name { font-weight: 600; font-size: 14px; color: var(--ink); }
.card-line { font-size: 12px; color: var(--ink2); margin-top: 2px; }
.card-tags { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }

/* ---------------- 二级：左侧功能栏 */
.frame { flex: 1; min-height: 0; display: flex; }
.side { flex: none; display: flex; flex-direction: column; background: #fff; border-right: 1px solid var(--line); transition: width .18s ease; overflow: hidden; }
.side-head { flex: none; padding: 16px 18px 12px; border-bottom: 1px solid var(--line); }
.folded .side-head { padding: 14px 0 12px; text-align: center; }
.side-title { font-size: 15px; font-weight: 700; color: var(--ink); }
.side-desc { font-size: 12px; color: var(--ink2); margin-top: 3px; line-height: 1.4; }
.side-icon { color: var(--brand); }
.side-scroll { flex: 1; min-height: 0; }
.items { padding: 8px; display: flex; flex-direction: column; gap: 2px; }
.item { position: relative; display: flex; align-items: center; gap: 10px; padding: 9px 10px; border-radius: 8px; color: var(--ink); transition: background .12s, color .12s; }
.folded .item { justify-content: center; padding: 10px 0; }
.item:hover { background: #f2f6fb; }
.item.on { background: #e8f1fb; color: var(--brand); }
.item.on::before { content: ''; position: absolute; left: 0; top: 10px; bottom: 10px; width: 3px; border-radius: 2px; background: var(--brand); }
.ic { position: relative; flex: none; width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center; background: #f2f5f9; color: #5a6b7d; }
.item.on .ic { background: #d6e7fa; color: var(--brand); }
.idot { position: absolute; top: -2px; right: -2px; width: 8px; height: 8px; border-radius: 50%; background: #f56c6c; border: 2px solid #fff; }
.txt { flex: 1; min-width: 0; display: flex; flex-direction: column; line-height: 1.3; }
.nm { font-size: 14px; font-weight: 600; }
.ds { font-size: 11.5px; color: var(--ink2); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.item.on .ds { color: #4f7fb5; }
.badge { flex: none; background: #f56c6c; color: #fff; font-size: 11px; line-height: 18px; min-width: 18px; text-align: center; padding: 0 5px; border-radius: 9px; }

/* ---------------- 内容区：页签 + 页面 */
.content { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.tabstrip { flex: none; height: 42px; display: flex; align-items: center; gap: 6px; padding: 0 12px 0 8px; background: #fff; border-bottom: 1px solid var(--line); }
.fold { flex: none; width: 30px; height: 30px; padding: 0; border: 0; border-radius: 6px; background: none; color: var(--ink2); cursor: pointer;
  display: grid; place-items: center; }
.fold:hover { background: #eef2f7; color: var(--brand); }
.main { flex: 1; min-height: 0; overflow: auto; padding: 20px; }

@media (max-width: 1360px) {
  .lookup { width: 220px; }
}
@media (max-width: 1180px) {
  .brand { width: auto; }
  .brand-title { display: none; }
  .mod { padding: 0 10px; }
}
</style>

<style>
/* 页面切换过渡 */
.page-enter-active, .page-leave-active { transition: opacity .15s ease, transform .15s ease; }
.page-enter-from { opacity: 0; transform: translateY(6px); }
.page-leave-to { opacity: 0; transform: translateY(-4px); }
</style>
