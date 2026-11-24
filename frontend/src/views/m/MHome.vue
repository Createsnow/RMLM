<template>
  <div>
    <router-link to="/m/scan" class="scanbtn">
      <el-icon :size="28"><Camera /></el-icon>
      <div><b>扫码登记</b><div class="s">扫仓储码 → 选动作 → 拍照 → 提交</div></div>
    </router-link>
    <div v-if="d" class="mk">
      <div class="c" @click="$router.push('/m/mine')"><b style="color: var(--brand)">{{ d.counts.active }}</b><span>在制结存行</span></div>
      <div class="c"><b style="color: #c0392b">{{ d.counts.overdue }}</b><span>超期未结案</span></div>
      <div class="c"><b style="color: #b46a00">{{ d.counts.scrap_pending }}</b><span>报废待批次处置</span></div>
      <div class="c"><b style="color: #5b4bc4">{{ d.counts.recon_open }}</b><span>对账差异</span></div>
    </div>
    <div class="grid">
      <router-link to="/m/apply" class="cell"><el-icon :size="22"><EditPen /></el-icon>领料申请</router-link>
      <router-link to="/m/mine" class="cell"><el-icon :size="22"><Tickets /></el-icon>我的领用</router-link>
      <router-link v-if="mgr" to="/m/approve" class="cell"><el-icon :size="22"><Stamp /></el-icon>待我审核</router-link>
      <router-link to="/m/reqs" class="cell"><el-icon :size="22"><Document /></el-icon>我的申请</router-link>
    </div>
    <template v-if="d && d.need_today.length">
      <div class="sec">需要处理的物料（{{ d.need_today.length }}）</div>
      <div v-for="l in d.need_today.slice(0, 10)" :key="l.id" class="item" @click="$router.push(`/m/line/${l.id}`)">
        <div class="tt"><b>{{ l.material }}</b><div class="muted">{{ l.project }} · {{ l.holder }} · 结存 {{ l.balance }} {{ l.unit }}</div></div>
        <el-tag size="small" :type="STATUS_TYPE[l.status]">{{ l.status === 'S5' ? '超期未结案' : '待批次处置' }}</el-tag>
      </div>
    </template>
    <div class="sec">待我处理（{{ todos.length }}）</div>
    <div v-for="t in todos.slice(0, 20)" :key="t.id" class="item" @click="go(t)">
      <el-tag size="small" :type="t.is_notice ? 'info' : t.level > 1 ? 'danger' : 'primary'">{{ TODO_KINDS[t.kind] || t.kind }}</el-tag>
      <div class="tt">{{ t.title }}<div class="muted">{{ t.created_at }}</div></div>
    </div>
    <el-empty v-if="!todos.length" description="暂无待办" :image-size="60" />
    <template v-if="d && d.recent_events.length">
      <div class="sec">最近登记</div>
      <div v-for="e in d.recent_events.slice(0, 3)" :key="e.id" class="item" @click="$router.push(`/m/line/${e.line_id}`)">
        <div class="tt"><b>{{ e.etype_name }} · {{ e.material }}</b><div class="muted">{{ e.created_at }} · {{ e.operator }}</div></div>
        <span v-if="+e.qty" class="num">{{ e.qty }} {{ e.unit }}</span>
      </div>
    </template>
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
import { STATUS_TYPE, TODO_KINDS, todoLink } from '../../utils'

const router = useRouter()
const todos = ref([])
const d = ref(null)
const mgr = computed(() => store.user?.is_manager || (store.user?.delegated_by || []).length)
function go(t) {
  if (t.is_notice) api.post(`/api/todos/${t.id}/read`)
  const link = todoLink(t, true)
  if (link) router.push(link.startsWith('/m') ? link : link)
}
function toPc() { sessionStorage.setItem('rmlm_pc', '1'); router.push('/') }
onMounted(async () => {
  todos.value = await api.get('/api/todos')
  d.value = await api.get('/api/reports/dashboard', { silent: true }).catch(() => null)
})
</script>

<style scoped>
.scanbtn { display: flex; align-items: center; gap: 14px; padding: 18px; border-radius: 14px; color: #fff;
  background: linear-gradient(135deg, #0b5cb8, #1f8fff); box-shadow: 0 6px 18px rgba(11, 92, 184, .25); }
.scanbtn .s { font-size: 12px; opacity: .85; margin-top: 2px; }
.grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 12px 0; }
.cell { background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 12px 4px; display: flex; flex-direction: column;
  align-items: center; gap: 6px; font-size: 12px; color: var(--ink); }
.mk { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin-top: 10px; }
.mk .c { background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 10px 4px; display: flex; flex-direction: column; align-items: center; gap: 2px; }
.mk b { font-size: 20px; font-variant-numeric: tabular-nums; }
.mk span { font-size: 11px; color: var(--ink2); text-align: center; }
.sec { font-weight: 800; font-size: 13px; margin: 14px 2px 8px; }
.item { display: flex; gap: 8px; align-items: flex-start; background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 10px; margin-bottom: 8px; }
.tt { font-size: 13px; flex: 1; }
</style>
