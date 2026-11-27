<template>
  <!-- 手机首页分层：扫码（最常用）→ 待我处理 → 常用功能 → 数据 → 最近登记 -->
  <div>
    <router-link to="/m/scan" class="scanbtn">
      <el-icon :size="28"><Camera /></el-icon>
      <div><b>扫码登记</b><div class="s">扫仓储码 → 选动作 → 拍照 → 提交</div></div>
    </router-link>

    <div class="sec">待我处理<span v-if="todos.length" class="cnt">{{ todos.length }}</span><span class="grow" /><router-link v-if="todos.length > 5" to="/m/todos" class="more">全部 ›</router-link></div>
    <div v-for="t in todos.slice(0, 5)" :key="t.id" class="item" @click="go(t)">
      <el-tag size="small" :type="t.is_notice ? 'info' : t.level > 1 ? 'danger' : 'primary'">{{ TODO_KINDS[t.kind] || t.kind }}</el-tag>
      <div class="tt">{{ t.title }}<div class="muted">{{ t.created_at }}</div></div>
    </div>
    <div v-if="loaded && !todos.length" class="none">没有待处理的事项</div>

    <div class="sec">常用功能</div>
    <div class="grid">
      <router-link v-for="c in cells" :key="c.to" :to="c.to" class="cell">
        <el-badge :value="c.badge" :hidden="!c.badge"><el-icon :size="22"><component :is="c.icon" /></el-icon></el-badge>{{ c.name }}
      </router-link>
    </div>

    <template v-if="d">
      <div class="sec">数据<span class="muted" style="margin-left: 6px; font-weight: 400">按您的权限</span></div>
      <div class="mk" :class="{ two: !ops }">
        <div class="c" @click="$router.push('/m/mine')"><b style="color: var(--brand)">{{ d.counts.active }}</b><span>在制结存行</span></div>
        <div class="c"><b :style="{ color: d.counts.overdue ? '#c0392b' : '' }">{{ d.counts.overdue }}</b><span>超期未结案</span></div>
        <template v-if="ops">
          <div class="c"><b style="color: #b46a00">{{ d.counts.scrap_pending }}</b><span>报废待批次处置</span></div>
          <div class="c"><b style="color: #5b4bc4">{{ d.counts.recon_open }}</b><span>对账差异</span></div>
        </template>
      </div>
      <template v-if="d.need_today.length">
        <div class="sec">需要处理的物料（{{ d.need_today.length }}）</div>
        <div v-for="l in d.need_today.slice(0, 10)" :key="l.id" class="item" @click="$router.push(`/m/line/${l.id}`)">
          <div class="tt"><b>{{ l.material }}</b><div class="muted">{{ l.project }} · {{ l.holder }} · 结存 {{ l.balance }} {{ l.unit }}</div></div>
          <el-tag size="small" :type="STATUS_TYPE[l.status]">{{ l.status === 'S5' ? '超期未结案' : '待批次处置' }}</el-tag>
        </div>
      </template>
      <template v-if="d.recent_events.length">
        <div class="sec">最近登记</div>
        <div v-for="e in d.recent_events.slice(0, 3)" :key="e.id" class="item" @click="$router.push(`/m/line/${e.line_id}`)">
          <div class="tt"><b>{{ e.etype_name }} · {{ e.material }}</b><div class="muted">{{ e.created_at }} · {{ e.operator }}</div></div>
          <span v-if="+e.qty" class="num">{{ e.qty }} {{ e.unit }}</span>
        </div>
      </template>
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
import { isOps } from '../../nav'

const router = useRouter()
const todos = ref([])
const loaded = ref(false)
const d = ref(null)
const ops = computed(() => isOps())
const approver = computed(() => store.user?.is_manager || (store.user?.delegated_by || []).length)
const byKind = k => (store.counts.by_kind || {})[k] || 0
/** 常用功能：人人都有的四项，审核人多一项「待我审核」 */
const cells = computed(() => [
  { to: '/m/apply', name: '新建申请', icon: 'EditPen' },
  { to: '/m/mine', name: '我的领用', icon: 'Tickets' },
  { to: '/m/reqs', name: '我的申请', icon: 'Document' },
  ...(approver.value ? [{ to: '/m/approve', name: '待我审核', icon: 'Stamp', badge: byKind('APPROVE') + byKind('LINE_APPROVE') }] : []),
  { to: '/m/todos', name: '我的待办', icon: 'Bell', badge: (store.counts.tasks || 0) + (store.counts.notices || 0) }
])
function go(t) {
  if (t.is_notice) api.post(`/api/todos/${t.id}/read`)
  const link = todoLink(t, true)
  if (link) router.push(link)
}
function toPc() { sessionStorage.setItem('rmlm_pc', '1'); router.push('/') }
onMounted(async () => {
  // 未读通知排在待办事项后面
  todos.value = (await api.get('/api/todos')).sort((a, b) => Number(a.is_notice) - Number(b.is_notice))
  loaded.value = true
  d.value = await api.get('/api/reports/dashboard', { silent: true }).catch(() => null)
})
</script>

<style scoped>
.scanbtn { display: flex; align-items: center; gap: 14px; padding: 18px; border-radius: 14px; color: #fff;
  background: linear-gradient(135deg, #0b5cb8, #1f8fff); box-shadow: 0 6px 18px rgba(11, 92, 184, .25); }
.scanbtn .s { font-size: 12px; opacity: .85; margin-top: 2px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(64px, 1fr)); gap: 8px; }
.cell { background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 12px 4px; display: flex; flex-direction: column;
  align-items: center; gap: 6px; font-size: 12px; color: var(--ink); }
.mk { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; }
.mk.two { grid-template-columns: repeat(2, 1fr); }
.mk .c { background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 10px 4px; display: flex; flex-direction: column; align-items: center; gap: 2px; }
.mk b { font-size: 20px; font-variant-numeric: tabular-nums; }
.mk span { font-size: 11px; color: var(--ink2); text-align: center; }
.sec { display: flex; align-items: center; font-weight: 800; font-size: 13px; margin: 16px 2px 8px; }
.cnt { margin-left: 6px; padding: 0 6px; border-radius: 9px; background: #f56c6c; color: #fff; font-size: 11px; line-height: 16px; font-weight: 600; }
.grow { flex: 1; }
.more { font-weight: 400; font-size: 12px; }
.none { background: #fff; border: 1px dashed var(--line); border-radius: 10px; padding: 14px; text-align: center; color: var(--ink2); font-size: 12.5px; }
.item { display: flex; gap: 8px; align-items: flex-start; background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 10px; margin-bottom: 8px; }
.tt { font-size: 13px; flex: 1; }
</style>
