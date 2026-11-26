<template>
  <div class="m">
    <div class="m-top">
      <el-icon v-if="$route.path !== '/m'" class="back" @click="$router.back()"><ArrowLeft /></el-icon>
      <span class="t">{{ $route.meta.title }}</span>
      <span class="grow" />
      <el-tag v-if="store.user?.frozen" type="danger" size="small">已冻结</el-tag>
      <span v-if="$route.path !== '/m/password'" class="pw" @click="$router.push('/m/password')"><el-icon><Lock /></el-icon>改密码</span>
    </div>
    <div class="m-body"><router-view :key="$route.fullPath" /></div>
    <div class="m-tab">
      <router-link v-for="t in tabs" :key="t.path" :to="t.path" :class="{ on: isOn(t.path) }">
        <el-badge :value="t.badge" :hidden="!t.badge"><el-icon :size="20"><component :is="t.icon" /></el-icon></el-badge>
        <span>{{ t.name }}</span>
      </router-link>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import { store, setUser } from '../store'
import { loadDicts } from '../utils'

const route = useRoute()
const tabs = computed(() => [
  { path: '/m', name: '首页', icon: 'HomeFilled', badge: store.counts.tasks },
  { path: '/m/scan', name: '扫码', icon: 'Camera' },
  { path: '/m/apply', name: '申请', icon: 'EditPen' },
  { path: '/m/mine', name: '我的', icon: 'User' },
  { path: '/m/ledger', name: '台账', icon: 'Tickets' }
])
const isOn = p => p === '/m' ? route.path === '/m' : route.path.startsWith(p)
onMounted(async () => {
  loadDicts()
  try {
    setUser(await api.get('/api/auth/me', { silent: true }))
    store.counts = await api.get('/api/todos/count', { silent: true })
  } catch { /* ignore */ }
})
</script>

<style scoped>
.m { min-height: 100vh; background: var(--bg); padding-bottom: 64px; }
.m-top { position: sticky; top: 0; z-index: 10; display: flex; align-items: center; gap: 8px; height: 48px; padding: 0 14px;
  background: linear-gradient(135deg, #0b5cb8, #1f8fff); color: #fff; }
.m-top .t { font-weight: 700; font-size: 16px; }
.back { font-size: 20px; cursor: pointer; }
.grow { flex: 1; }
.pw { display: inline-flex; align-items: center; gap: 3px; font-size: 13px; color: var(--brand); cursor: pointer; }
.m-body { padding: 12px; }
.m-tab { position: fixed; bottom: 0; left: 0; right: 0; height: 58px; background: #fff; border-top: 1px solid var(--line);
  display: grid; grid-template-columns: repeat(5, 1fr); z-index: 10; padding-bottom: env(safe-area-inset-bottom); }
.m-tab a { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px; font-size: 11px; color: #8a99ab; }
.m-tab a.on { color: var(--brand); font-weight: 700; }
</style>
