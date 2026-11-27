<template>
  <!--
    首页（工作台）按重要程度分层，从上到下：
    1 我是谁 + 两个最常用的操作；2 待我处理 + 常用功能（按岗位）；3 业务流程（标出我负责的环节）；4 数据概览（按权限）
  -->
  <div class="wb">
    <section class="hello">
      <div class="who">
        <h2>{{ greet }}，{{ store.user?.name }}</h2>
        <div class="muted">
          <el-tag v-for="r in store.user?.role_names || []" :key="r" size="small" effect="plain" class="flag">{{ r }}</el-tag>
          <span>{{ dateText }}<template v-if="store.user?.manager_name"> · 直属上级 {{ store.user.manager_name }}</template></span>
        </div>
      </div>
      <span class="grow" />
      <el-button size="large" @click="go('/scan')"><el-icon><Camera /></el-icon><span>扫码登记</span></el-button>
      <el-button size="large" type="primary" @click="go('/apply')"><el-icon><Plus /></el-icon><span>新建申请</span></el-button>
    </section>
    <el-alert v-if="store.user?.frozen" type="error" show-icon :closable="false" class="frozen"
      title="您的新领用已冻结：名下有超期未处理的物料。请先在「领用台账」处理超期物料，再由直属上级解冻。" />

    <div class="row2">
      <section class="card todo-card">
        <div class="card-head">
          <h3>待我处理 <span v-if="openTodos.length" class="cnt">{{ openTodos.length }}</span></h3>
          <span class="grow" />
          <a class="more" href="/todos" @click.prevent="go('/todos')">全部待办 <el-icon><ArrowRight /></el-icon></a>
        </div>
        <div v-if="groups.length" class="chips">
          <button v-for="g in groups" :key="g.kind" type="button" class="chip" :class="{ note: g.notice }" @click="go(`/todos?kind=${g.kind}`)">
            {{ TODO_KINDS[g.kind] || g.kind }}<b>{{ g.n }}</b>
          </button>
        </div>
        <div v-for="t in openTodos.slice(0, 6)" :key="t.id" class="todo" @click="openTodo(t)">
          <el-tag size="small" :type="t.is_notice ? 'info' : t.level > 1 ? 'danger' : 'primary'">{{ TODO_KINDS[t.kind] || t.kind }}</el-tag>
          <div class="tt">
            <div class="t1">{{ t.title }}</div>
            <div class="muted">{{ t.created_at }}<span v-if="t.due_at" :class="{ late: t.overdue }"> · 期限 {{ t.due_at }}</span></div>
          </div>
          <el-icon class="go"><ArrowRight /></el-icon>
        </div>
        <div v-if="todosLoaded && !openTodos.length" class="empty">
          <el-icon :size="28"><CircleCheck /></el-icon>
          <div>没有待处理的事项</div>
        </div>
      </section>

      <section class="card quick-card">
        <div class="card-head"><h3>常用功能</h3></div>
        <template v-for="grp in quickGroups" :key="grp.title">
          <div class="qgroup">{{ grp.title }}</div>
          <div class="qgrid">
            <a v-for="q in grp.items" :key="q.path" class="qitem" :href="q.path" @click.prevent="go(q.path)">
              <span class="qi"><el-icon :size="18"><component :is="q.icon" /></el-icon></span>
              <span class="qt"><b>{{ q.name }}</b><span>{{ q.desc }}</span></span>
              <span v-if="q.badge" class="qb">{{ q.badge }}</span>
            </a>
          </div>
        </template>
      </section>
    </div>

    <section class="card">
      <div class="card-head">
        <h3>业务流程</h3>
        <span class="muted">一份物料从申请到关账的七个环节；<i class="lg" />为您要动手的环节，点击进入</span>
      </div>
      <ol class="flow">
        <li v-for="s in flow" :key="s.no" :class="{ mine: s.mine, link: !!s.path }" :title="s.path ? `进入${s.name}` : `由${s.who}处理`"
          @click="s.path && go(s.path)">
          <span class="no">{{ s.no }}</span>
          <b class="nm">{{ s.name }}</b>
          <span class="whom">{{ s.who }}</span>
          <span class="hint">{{ s.hint }}</span>
        </li>
      </ol>
    </section>

    <template v-if="d">
      <div class="sec-title">数据概览 <span class="muted">数据范围按您的权限 · 截至 {{ d.today }} · 在制口径：领用 − 耗用 − 损耗 − 退库 − 报废</span></div>
      <div class="kpis">
        <a class="kpi b link" @click="go('/ledger?status=active')"><div class="l">在制结存行数</div><div class="v">{{ d.counts.active }}</div><div class="s">涉及 {{ d.counts.projects }} 个项目 · ¥{{ fmtMoney(d.counts.balance_amount) }}</div></a>
        <a class="kpi link" :class="{ bad: d.counts.overdue }" @click="go('/ledger?status=S5')"><div class="l">超期未结案</div><div class="v">{{ d.counts.overdue }}</div><div class="s">超预计归还日仍未处置</div></a>
        <div class="kpi g"><div class="l">今日登记</div><div class="v">{{ d.counts.today_events }}</div><div class="s">事件流水条数</div></div>
        <template v-if="ops">
          <a class="kpi a" :class="{ link: can('/dispose') }" @click="can('/dispose') && go('/dispose?tab=scrap')"><div class="l">报废待批次处置</div><div class="v">{{ d.counts.scrap_pending }}</div><div class="s">实物在暂存区，缺 ERP 报废单</div></a>
          <a class="kpi p" :class="{ link: can('/recon') }" @click="can('/recon') && go('/recon')"><div class="l">ERP 对账差异</div><div class="v">{{ d.counts.recon_open }}</div><div class="s">需 48 小时内认领</div></a>
        </template>
      </div>

      <div class="row-data">
        <div class="card">
          <h3>需要处理的物料 <span class="muted">超期 + 待报废处置</span></h3>
          <el-table :data="d.need_today.slice(0, 8)" size="small" :show-header="false" empty-text="没有需要处理的物料">
            <el-table-column><template #default="{ row }"><b>{{ row.material }}</b><div class="muted">{{ row.project }} · {{ row.holder }} · <span class="mono">{{ row.code }}</span></div></template></el-table-column>
            <el-table-column width="104"><template #default="{ row }"><el-tag :type="STATUS_TYPE[row.status]" size="small">{{ statusName(row.status) }}</el-tag></template></el-table-column>
            <el-table-column width="70" align="right"><template #default="{ row }"><el-button size="small" @click="go(`/line/${row.id}`)">处理</el-button></template></el-table-column>
          </el-table>
          <div v-if="d.need_today.length > 8" class="muted foot">另有 {{ d.need_today.length - 8 }} 行，见 <a href="/ledger" @click.prevent="go('/ledger?status=S5')">领用台账</a></div>
        </div>
        <div class="card">
          <h3>在制结存 · 按研发项目 <span class="muted">行数口径</span></h3>
          <div v-for="p in d.by_project.slice(0, 8)" :key="p.project" class="hbar" :title="p.name">
            <div class="k">{{ p.project }}</div>
            <div class="track"><div class="fill" :style="{ width: `${(p.lines / maxLines * 100).toFixed(0)}%` }" /></div>
            <div class="n">{{ p.lines }} 行</div>
          </div>
          <el-empty v-if="!d.by_project.length" description="暂无在制物料" :image-size="50" />
          <div v-if="d.by_project.length > 8" class="muted foot">另有 {{ d.by_project.length - 8 }} 个项目，见 <a href="/reports" @click.prevent="go('/reports?tab=projects')">统计报表 → 项目视图</a></div>
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <h3>最近流向事件 <span class="muted">只增不改 · 每条都有经手人与时间</span></h3>
          <span class="grow" />
          <a v-if="ops" class="more" href="/reports" @click.prevent="go('/reports?tab=metrics')">
            一期验收门槛 <el-tag :type="d.gate.passed ? 'success' : 'danger'" size="small">{{ d.gate.passed ? '已达标' : '未达标' }}</el-tag> <el-icon><ArrowRight /></el-icon>
          </a>
        </div>
        <el-table :data="d.recent_events.slice(0, 8)" size="small" class="clickable" empty-text="暂无登记" @row-click="r => go(`/line/${r.line_id}`)">
          <el-table-column prop="created_at" label="时间" width="160" />
          <el-table-column label="事件" width="110"><template #default="{ row }"><el-tag :type="EV_TYPE[row.etype] || 'primary'" size="small">{{ row.etype_name }}</el-tag></template></el-table-column>
          <el-table-column label="数量" width="100" align="right"><template #default="{ row }"><span class="num">{{ +row.qty ? `${row.qty} ${row.unit}` : '—' }}</span></template></el-table-column>
          <el-table-column prop="material" label="物料" min-width="120" />
          <el-table-column label="去向 / 位置" min-width="130"><template #default="{ row }">{{ row.dest ? dictName('DEST', row.dest) : '' }}{{ row.where ? ` ${row.where}` : '' }}{{ !row.dest && !row.where ? '—' : '' }}</template></el-table-column>
          <el-table-column label="经手人" width="110"><template #default="{ row }">{{ row.operator }}<el-tag v-if="row.is_proxy" size="small" type="warning" style="margin-left: 4px">代录</el-tag></template></el-table-column>
          <el-table-column label="ERP 单据" width="170"><template #default="{ row }">
            <span v-if="row.erp_doc_no" class="mono">{{ row.erp_doc_no }}</span>
            <span v-else-if="row.needs_doc" class="late">未关联</span>
            <span v-else class="muted">—</span>
          </template></el-table-column>
        </el-table>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { store } from '../store'
import { STATUS_TYPE, TODO_KINDS, dictName, fmtMoney, todoLink } from '../utils'
import { badgeOf, canOpen, flowSteps, isOps, quickEntries, visibleModules } from '../nav'
import { findTab } from '../tabs'
import { useRefresh } from '../useRefresh'

const router = useRouter()
const d = ref(null)
const todos = ref([])
const todosLoaded = ref(false)

const EV_TYPE = { SCRAP: 'danger', RETURN: 'success', TRANSFER_OK: '', TRANSFER_REQ: '', HOLD: 'info', MOVE: 'info', SIGN: 'primary', CONCLUSION: 'success' }
const statusName = k => k === 'VOID' ? '已作废' : (dictName('LINE_STATUS', k).replace(/^S\d /, '') || k)
const maxLines = computed(() => Math.max(1, ...d.value.by_project.map(p => p.lines)))

const greet = computed(() => {
  const h = new Date().getHours()
  return h < 5 ? '夜深了' : h < 11 ? '上午好' : h < 13 ? '中午好' : h < 18 ? '下午好' : '晚上好'
})
const dateText = (() => {
  const t = new Date()
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')} 星期${'日一二三四五六'[t.getDay()]}`
})()

// ---------------- 导航（与侧栏同一份配置）

const mods = computed(() => visibleModules())
const can = path => canOpen(path, mods.value)
const ops = computed(() => isOps())
/** 带参数的地址按新地址打开；不带参数的功能已打开过就切回原页签 */
function go(path) {
  router.push(path.includes('?') ? path : (findTab(path)?.fullPath ?? path))
}
const personal = computed(() => [
  { path: '/ledger?scope=mine', name: '我持有的物料', icon: 'Tickets', desc: '结存、归还日，登记使用 / 退库' },
  { path: '/reqs', name: '我的申请', icon: 'EditPen', desc: '审核与开单进度' }
])
const roleQuick = computed(() => quickEntries(mods.value).filter(q => q.path !== '/reqs' && q.path !== '/ledger')
  .map(q => ({ ...q, badge: badgeOf(q) })))
/** 常用功能分两组：「我的」人人都有；「岗位功能」按角色（审核人、领料专员、财务…） */
const quickGroups = computed(() => [{ title: '我的', items: personal.value }, { title: '岗位功能', items: roleQuick.value }]
  .filter(g => g.items.length))
const flow = computed(() => flowSteps(mods.value))

// ---------------- 待办

const openTodos = computed(() => [...todos.value].sort((a, b) => Number(a.is_notice) - Number(b.is_notice)))
const groups = computed(() => {
  const m = new Map()
  for (const t of openTodos.value) {
    const g = m.get(t.kind) || { kind: t.kind, n: 0, notice: t.is_notice }
    g.n++
    m.set(t.kind, g)
  }
  return [...m.values()]
})
function openTodo(t) {
  if (t.is_notice) api.post(`/api/todos/${t.id}/read`).catch(() => {})
  const link = todoLink(t)
  if (link) go(link)
}

useRefresh(async () => {
  const [dash, list] = await Promise.all([api.get('/api/reports/dashboard'), api.get('/api/todos?state=OPEN')])
  d.value = dash
  todos.value = list
  todosLoaded.value = true
})
</script>

<style scoped>
.wb { max-width: 1440px; }
.grow { flex: 1; }
.flag { margin-right: 4px; }
.late { color: #c0392b; }

/* 第一层：问候 + 主操作 */
.hello { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; padding: 18px 20px; border-radius: 12px;
  background: linear-gradient(120deg, #ffffff 0%, #eef5fd 100%); border: 1px solid var(--line); }
.hello h2 { margin: 0 0 6px; font-size: 20px; }
.hello .muted { display: flex; align-items: center; flex-wrap: wrap; gap: 2px; font-size: 12.5px; }
.hello .el-button .el-icon + span { margin-left: 6px; }
.frozen { margin-bottom: 14px; }

/* 第二层：待办 + 常用功能 */
.row2 { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(300px, 1fr); gap: 14px; }
.todo-card { display: flex; flex-direction: column; }
.card-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
.card-head h3 { margin: 0; }
.cnt { display: inline-block; margin-left: 4px; padding: 0 7px; border-radius: 10px; background: #f56c6c; color: #fff; font-size: 12px; line-height: 18px; vertical-align: 2px; }
.more { display: inline-flex; align-items: center; gap: 4px; font-size: 13px; cursor: pointer; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }
.chip { border: 1px solid #cfe0f5; background: #f3f8fe; color: var(--ink); border-radius: 16px; padding: 3px 12px; font: inherit; font-size: 12.5px; cursor: pointer; }
.chip b { margin-left: 6px; color: var(--brand); }
.chip.note { border-color: var(--line); background: #f7f8fa; }
.chip.note b { color: var(--ink2); }
.chip:hover { border-color: var(--brand); }
.todo { display: flex; align-items: center; gap: 10px; padding: 10px 4px; border-top: 1px solid var(--line); cursor: pointer; }
.todo:hover { background: #f7f9fc; }
.todo .tt { flex: 1; min-width: 0; }
.todo .t1 { font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.todo .go { color: #b6c0ce; }
.empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px; padding: 28px 0; color: #5fb38a; font-size: 13px; }
.empty div { color: var(--ink2); }
.qgroup { font-size: 12px; color: var(--ink2); margin: 4px 2px 6px; }
.qgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(196px, 1fr)); gap: 2px 8px; margin-bottom: 10px; }
.qitem { display: flex; align-items: center; gap: 10px; padding: 8px; border-radius: 8px; color: var(--ink); }
.qitem:hover { background: #f2f6fb; }
.qi { flex: none; width: 34px; height: 34px; border-radius: 9px; display: grid; place-items: center; background: #e8f1fb; color: var(--brand); }
.qt { flex: 1; min-width: 0; display: flex; flex-direction: column; line-height: 1.35; }
.qt b { font-size: 13.5px; }
.qt span { font-size: 12px; color: var(--ink2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.qb { flex: none; background: #f56c6c; color: #fff; font-size: 11px; line-height: 18px; min-width: 18px; text-align: center; padding: 0 5px; border-radius: 9px; }

/* 第三层：业务流程 */
.lg { display: inline-block; width: 10px; height: 10px; border-radius: 3px; background: var(--brand); margin: 0 3px; vertical-align: -1px; }
.flow { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 10px; counter-reset: none; }
.flow li { position: relative; display: flex; flex-direction: column; gap: 3px; padding: 12px 12px 12px; border: 1px solid var(--line); border-radius: 10px;
  background: #fafbfc; color: var(--ink2); }
.flow li:not(:last-child)::after { content: ''; position: absolute; right: -9px; top: 50%; width: 8px; height: 8px; border-top: 2px solid #c3cdd9;
  border-right: 2px solid #c3cdd9; transform: translateY(-50%) rotate(45deg); }
.flow li.mine { border-color: #9cc3ee; background: #f0f6fd; color: var(--ink); }
.flow li.link { cursor: pointer; }
.flow li.link:hover { border-color: var(--brand); box-shadow: 0 2px 10px rgba(11, 92, 184, .12); }
.flow .no { width: 22px; height: 22px; border-radius: 50%; display: grid; place-items: center; font-size: 12px; font-weight: 700; background: #e4e9f0; color: var(--ink2); }
.flow li.mine .no { background: var(--brand); color: #fff; }
.flow .nm { font-size: 13.5px; margin-top: 4px; }
.flow .whom { font-size: 12px; }
.flow .hint { font-size: 11.5px; color: #8a99ab; line-height: 1.4; }

/* 第四层：数据概览 */
.sec-title { font-weight: 700; font-size: 15px; margin: 22px 2px 10px; display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.sec-title .muted { font-weight: 400; }
.kpi.link { cursor: pointer; color: inherit; transition: border-color .12s, box-shadow .12s; }
.kpi.link:hover { border-color: #a9c8ec; box-shadow: 0 2px 10px rgba(11, 92, 184, .08); }
.row-data { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; }
.foot { margin-top: 8px; }
.clickable :deep(.el-table__row) { cursor: pointer; }

@media (max-width: 1280px) {
  .flow { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .flow li:nth-child(4)::after { display: none; }
}
@media (max-width: 1000px) {
  .row2, .row-data { grid-template-columns: 1fr; }
}
</style>
