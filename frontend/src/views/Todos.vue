<template>
  <div>
    <div class="page-head">
      <h2 v-if="!mobile">我的待办</h2>
      <el-radio-group v-model="state" size="small" @change="load">
        <el-radio-button value="OPEN">未完成</el-radio-button>
        <el-radio-button value="DONE">已完成</el-radio-button>
      </el-radio-group>
    </div>
    <div v-if="transferId" class="card">
      <h3>确认接收转交</h3>
      <div class="muted" style="margin-bottom: 10px">确认后您成为持有人，超期责任随之转移。</div>
      <el-button type="primary" @click="decideTransfer(true)">确认接收</el-button>
      <el-button @click="decideTransfer(false)">拒绝</el-button>
    </div>
    <div class="card" style="padding: 0">
      <div v-for="t in rows" :key="t.id" class="todo" @click="go(t)">
        <el-tag size="small" :type="t.is_notice ? 'info' : t.level > 1 ? 'danger' : 'primary'">{{ t.level > 1 ? `${t.level} 级` : '' }}{{ TODO_KINDS[t.kind] || t.kind }}</el-tag>
        <div class="tt">
          <div>{{ t.title }}</div>
          <div class="muted">{{ t.created_at }}<span v-if="t.due_at" :style="{ color: t.overdue ? '#c0392b' : '' }"> · 期限 {{ t.due_at }}</span></div>
        </div>
        <el-button v-if="t.is_notice && t.state === 'OPEN'" size="small" text @click.stop="read(t)">已读</el-button>
      </div>
      <el-empty v-if="!rows.length" description="暂无" />
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { store } from '../store'
import { TODO_KINDS, todoLink } from '../utils'
import { useRefresh } from '../useRefresh'

const route = useRoute()
const router = useRouter()
const mobile = computed(() => route.path.startsWith('/m'))
const state = ref('OPEN')
const rows = ref([])
const transferId = computed(() => route.query.transfer)

async function load() {
  rows.value = await api.get(`/api/todos?state=${state.value}`)
  store.counts = await api.get('/api/todos/count')
}
async function read(t) { await api.post(`/api/todos/${t.id}/read`); load() }
function go(t) {
  const link = todoLink(t, mobile.value)
  if (t.is_notice && t.state === 'OPEN') api.post(`/api/todos/${t.id}/read`)
  if (link) router.push(link)
}
async function decideTransfer(accept) {
  const line = await api.post(`/api/transfers/${transferId.value}/decide?accept=${accept}`)
  ElMessage.success(accept ? '已接收' : '已拒绝')
  router.replace(`${mobile.value ? '/m' : ''}/line/${line.id}`)
}
useRefresh(load)
</script>

<style scoped>
.todo { display: flex; align-items: center; gap: 10px; padding: 12px 14px; border-bottom: 1px solid var(--line); cursor: pointer; }
.todo:hover { background: #f7f9fc; }
.tt { flex: 1; font-size: 13.5px; }
</style>
