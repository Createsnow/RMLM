<template>
  <div v-if="d">
    <div class="page-head"><h2>首页</h2><span class="sub">数据截至 {{ d.today }} · 数据范围按您的权限 · 在制口径：领用 − 耗用 − 损耗 − 退库 − 报废</span></div>
    <div class="kpis">
      <div class="kpi b"><div class="l">在制结存行数</div><div class="v">{{ d.counts.active }}</div><div class="s">涉及 {{ d.counts.projects }} 个项目 · ¥{{ fmtMoney(d.counts.balance_amount) }}</div></div>
      <div class="kpi" :class="{ bad: d.counts.overdue }"><div class="l">超期未结案</div><div class="v">{{ d.counts.overdue }}</div><div class="s">超预计归还日仍未处置</div></div>
      <div class="kpi a"><div class="l">报废待批次处置</div><div class="v">{{ d.counts.scrap_pending }}</div><div class="s">实物在暂存区，缺 ERP 报废单</div></div>
      <div class="kpi p"><div class="l">ERP 对账差异</div><div class="v">{{ d.counts.recon_open }}</div><div class="s">需 48 小时内认领</div></div>
      <div class="kpi g"><div class="l">今日登记</div><div class="v">{{ d.counts.today_events }}</div><div class="s">事件流水条数</div></div>
      <div class="kpi"><div class="l">我的待办</div><div class="v">{{ d.todos }}</div><div class="s"><router-link to="/todos">去处理 →</router-link></div></div>
    </div>

    <el-row :gutter="14">
      <el-col :md="13" :xs="24">
        <div class="card">
          <h3>在制结存 · 按研发项目 <span class="muted">行数口径</span></h3>
          <div v-for="p in d.by_project.slice(0, 10)" :key="p.project" class="hbar" :title="p.name">
            <div class="k">{{ p.project }}</div>
            <div class="track"><div class="fill" :style="{ width: `${(p.lines / maxLines * 100).toFixed(0)}%` }" /></div>
            <div class="n">{{ p.lines }} 行</div>
          </div>
          <el-empty v-if="!d.by_project.length" description="暂无在制物料" :image-size="50" />
          <div v-if="d.by_project.length > 10" class="muted">另有 {{ d.by_project.length - 10 }} 个项目，见「报表 → 项目视图」</div>
        </div>
      </el-col>
      <el-col :md="11" :xs="24">
        <div class="card">
          <h3>需要今天处理 <span class="muted">超期 + 待报废处置</span></h3>
          <el-table :data="d.need_today" size="small" :show-header="false" empty-text="暂无待办">
            <el-table-column><template #default="{ row }"><b>{{ row.material }}</b><div class="muted">{{ row.project }} · {{ row.holder }} · <span class="mono">{{ row.code }}</span></div></template></el-table-column>
            <el-table-column width="104"><template #default="{ row }"><el-tag :type="STATUS_TYPE[row.status]" size="small">{{ statusName(row.status) }}</el-tag></template></el-table-column>
            <el-table-column width="70" align="right"><template #default="{ row }"><el-button size="small" @click="$router.push(`/line/${row.id}`)">处理</el-button></template></el-table-column>
          </el-table>
        </div>
      </el-col>
    </el-row>

    <div class="card">
      <h3>最近流向事件 <span class="muted">只增不改 · 每条都有经手人与时间</span></h3>
      <el-table :data="d.recent_events" size="small" @row-click="r => $router.push(`/line/${r.line_id}`)" style="cursor: pointer">
        <el-table-column prop="created_at" label="时间" width="160" />
        <el-table-column label="事件" width="110"><template #default="{ row }"><el-tag :type="EV_TYPE[row.etype] || 'primary'" size="small">{{ row.etype_name }}</el-tag></template></el-table-column>
        <el-table-column label="数量" width="100" align="right"><template #default="{ row }"><span class="num">{{ +row.qty ? `${row.qty} ${row.unit}` : '—' }}</span></template></el-table-column>
        <el-table-column prop="material" label="物料" min-width="120" />
        <el-table-column label="去向 / 位置" min-width="130"><template #default="{ row }">{{ row.dest ? dictName('DEST', row.dest) : '' }}{{ row.where ? ` ${row.where}` : '' }}{{ !row.dest && !row.where ? '—' : '' }}</template></el-table-column>
        <el-table-column label="经手人" width="100"><template #default="{ row }">{{ row.operator }}<el-tag v-if="row.is_proxy" size="small" type="warning" style="margin-left: 4px">代录</el-tag></template></el-table-column>
        <el-table-column label="ERP 单据" width="170"><template #default="{ row }">
          <span v-if="row.erp_doc_no" class="mono">{{ row.erp_doc_no }}</span>
          <span v-else-if="row.needs_doc" style="color: #c0392b">未关联</span>
          <span v-else class="muted">—</span>
        </template></el-table-column>
      </el-table>
    </div>

    <div class="card">
      <h3>一期验收六个数字 <el-tag :type="d.gate.passed ? 'success' : 'danger'" size="small">三项门槛{{ d.gate.passed ? '已达标' : '未达标' }}</el-tag></h3>
      <el-table :data="metricRows" size="small">
        <el-table-column prop="name" label="指标" />
        <el-table-column label="类别" width="90"><template #default="{ row }"><el-tag :type="row.gate ? 'danger' : 'info'" size="small" effect="plain">{{ row.gate ? '门槛' : '观察' }}</el-tag></template></el-table-column>
        <el-table-column prop="target" label="目标" width="110" />
        <el-table-column label="当前" width="100"><template #default="{ row }"><b :style="{ color: row.v == null ? '' : row.ok ? '#1c7f47' : '#c0392b' }">{{ row.v ?? '—' }}{{ row.v != null ? '%' : '' }}</b></template></el-table-column>
        <el-table-column prop="how" label="取数方式" min-width="220" />
      </el-table>
      <div class="muted" style="margin-top: 8px">审核 24 小时完成率 {{ pct(d.observe.approve_24h_rate) }} · 损耗率 {{ pct(d.observe.loss_rate) }}（只观察，不拦截）</div>
    </div>

    <div class="card">
      <h3>行状态分布</h3>
      <el-space wrap>
        <el-tag v-for="(n, k) in d.status" :key="k" :type="STATUS_TYPE[k]">{{ statusName(k) }}：{{ n }}</el-tag>
      </el-space>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { api } from '../api'
import { STATUS_TYPE, fmtMoney, dictName } from '../utils'
import { useRefresh } from '../useRefresh'

const d = ref(null)
const EV_TYPE = { SCRAP: 'danger', RETURN: 'success', TRANSFER_OK: '', TRANSFER_REQ: '', HOLD: 'info', MOVE: 'info', SIGN: 'primary', CONCLUSION: 'success' }
const statusName = k => k === 'VOID' ? '已作废' : (dictName('LINE_STATUS', k).replace(/^S\d /, '') || k)
const pct = v => v == null ? '—' : `${v}%`
const maxLines = computed(() => Math.max(1, ...d.value.by_project.map(p => p.lines)))
const metricRows = computed(() => {
  const g = d.value.gate, o = d.value.observe
  const row = (name, gate, target, v, ok, how) => ({ name, gate, target, v, ok: v == null || ok, how })
  return [
    row('项目关联率', true, '100%', g.project_link_rate, g.project_link_rate === 100, '领用行项目命中 ERP 项目档案的占比'),
    row('码绑定率', true, '≥ 95%', g.code_bind_rate, g.code_bind_rate >= 95, '已绑仓储条码的行 / 已签收行'),
    row('代录率（签收除外）', true, '< 10%', g.proxy_rate, g.proxy_rate < 10, '经手人 ≠ 持有人的事件占比'),
    row('对账差异关闭率', false, '月末 100%', o.recon_close_rate, o.recon_close_rate === 100, '对账差异中已平的占比'),
    row('报废单据覆盖率', false, '≥ 95%', o.scrap_doc_rate, o.scrap_doc_rate >= 95, '已挂 ERP 报废单的整件报废 / 已判废'),
    row('超期挂账清理率', false, '首季 ≥ 80%', o.overdue_clear_rate, o.overdue_clear_rate >= 80, '本季清理行数 / 季初挂账行数')
  ]
})
useRefresh(async () => { d.value = await api.get('/api/reports/dashboard') })
</script>
