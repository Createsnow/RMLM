<template>
  <div>
    <router-link to="/m/scan" class="scanbtn">
      <el-icon :size="28"><Camera /></el-icon>
      <div><b>扫码登记</b><div class="s">扫物料码 → 选动作 → 拍照 → 提交</div></div>
    </router-link>
    <div class="grid">
      <router-link to="/m/apply" class="cell"><el-icon :size="22"><EditPen /></el-icon>领料申请</router-link>
      <router-link to="/m/mine" class="cell"><el-icon :size="22"><Tickets /></el-icon>我的领用</router-link>
      <router-link v-if="mgr" to="/m/approve" class="cell"><el-icon :size="22"><Stamp /></el-icon>待我审核</router-link>
      <router-link to="/m/reqs" class="cell"><el-icon :size="22"><Document /></el-icon>我的申请</router-link>
    </div>
    <div class="sec">待我处理（{{ todos.length }}）</div>
    <div v-for="t in todos.slice(0, 20)" :key="t.id" class="item" @click="go(t)">
      <el-tag size="small" :type="t.is_notice ? 'info' : t.level > 1 ? 'danger' : 'primary'">{{ TODO_KINDS[t.kind] || t.kind }}</el-tag>
      <div class="tt">{{ t.title }}<div class="muted">{{ t.created_at }}</div></div>
    </div>
    <el-empty v-if="!todos.length" description="暂无待办" :image-size="60" />
    <div style="text-align: center; margin-top: 16px">
      <el-button text @click="toPc">电脑版</el-button>
      <el-button text @click="$router.push('/m/password')">修改密码 / 退出</el-button>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api'
import { store } from '../../store'
import { TODO_KINDS, todoLink } from '../../utils'

const router = useRouter()
const todos = ref([])
const mgr = computed(() => store.user?.is_manager || (store.user?.delegated_by || []).length)
function go(t) {
  if (t.is_notice) api.post(`/api/todos/${t.id}/read`)
  const link = todoLink(t, true)
  if (link) router.push(link.startsWith('/m') ? link : link)
}
function toPc() { sessionStorage.setItem('rmlm_pc', '1'); router.push('/') }
onMounted(async () => { todos.value = await api.get('/api/todos') })
</script>

<style scoped>
.scanbtn { display: flex; align-items: center; gap: 14px; padding: 18px; border-radius: 14px; color: #fff;
  background: linear-gradient(135deg, #0b5cb8, #1f8fff); box-shadow: 0 6px 18px rgba(11, 92, 184, .25); }
.scanbtn .s { font-size: 12px; opacity: .85; margin-top: 2px; }
.grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 12px 0; }
.cell { background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 12px 4px; display: flex; flex-direction: column;
  align-items: center; gap: 6px; font-size: 12px; color: var(--ink); }
.sec { font-weight: 800; font-size: 13px; margin: 14px 2px 8px; }
.item { display: flex; gap: 8px; align-items: flex-start; background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 10px; margin-bottom: 8px; }
.tt { font-size: 13px; flex: 1; }
</style>
