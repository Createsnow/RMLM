<template>
  <div v-if="d">
    <div class="page-head"><h2>总览看板</h2><span class="sub">数据范围按您的权限 · {{ d.today }}</span></div>
    <div class="kpis">
      <div class="kpi"><div class="l">在制结存行</div><div class="v">{{ d.counts.active }}</div><div class="s">结存金额 ¥{{ fmtMoney(d.counts.balance_amount) }}</div></div>
      <div class="kpi" :class="{ bad: d.counts.overdue }"><div class="l">超期未结案</div><div class="v">{{ d.counts.overdue }}</div><div class="s">超预计归还日</div></div>
      <div class="kpi" :class="{ bad: d.counts.urgent_pending }"><div class="l">紧急·待补</div><div class="v">{{ d.counts.urgent_pending }}</div><div class="s">24 小时内补审核与 ERP 单</div></div>
      <div class="kpi"><div class="l">待单据处置</div><div class="v">{{ d.counts.doc_pending }}</div><div class="s">已退 / 已报废未挂 ERP 单</div></div>
      <div class="kpi"><div class="l">待签收</div><div class="v">{{ d.counts.to_sign }}</div><div class="s">已开单未本人签收</div></div>
      <div class="kpi"><div class="l">我的待办</div><div class="v">{{ d.todos }}</div><div class="s"><router-link to="/todos">去处理 →</router-link></div></div>
    </div>
    <el-row :gutter="14">
      <el-col :md="12" :xs="24">
        <div class="card">
          <h3>一期验收门槛（三项同时达标） <el-tag :type="d.gate.passed ? 'success' : 'danger'" size="small">{{ d.gate.passed ? '达标' : '未达标' }}</el-tag></h3>
          <el-table :data="gateRows" size="small">
            <el-table-column prop="name" label="指标" />
            <el-table-column prop="target" label="目标" width="90" />
            <el-table-column label="当前" width="100"><template #default="{ row }"><b :style="{ color: row.ok ? '#1c7f47' : '#c0392b' }">{{ row.v ?? '—' }}{{ row.v != null ? '%' : '' }}</b></template></el-table-column>
          </el-table>
        </div>
      </el-col>
      <el-col :md="12" :xs="24">
        <div class="card">
          <h3>观察指标（不卡验收）</h3>
          <el-table :data="obsRows" size="small">
            <el-table-column prop="name" label="指标" />
            <el-table-column prop="target" label="参考" width="110" />
            <el-table-column label="当前" width="100"><template #default="{ row }">{{ row.v ?? '—' }}{{ row.v != null ? '%' : '' }}</template></el-table-column>
          </el-table>
        </div>
      </el-col>
    </el-row>
    <div class="card">
      <h3>行状态分布</h3>
      <el-space wrap>
        <el-tag v-for="(n, k) in d.status" :key="k" :type="STATUS_TYPE[k]">{{ statusName(k) }}：{{ n }}</el-tag>
      </el-space>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { STATUS_TYPE, fmtMoney, dictName } from '../utils'

const d = ref(null)
const statusName = k => k === 'VOID' ? '已作废' : dictName('LINE_STATUS', k)
const gateRows = computed(() => {
  const g = d.value.gate
  return [
    { name: '项目关联率', target: '100%', v: g.project_link_rate, ok: g.project_link_rate == null || g.project_link_rate === 100 },
    { name: '码绑定率', target: '≥ 95%', v: g.code_bind_rate, ok: g.code_bind_rate == null || g.code_bind_rate >= 95 },
    { name: '代登率（签收除外）', target: '< 10%', v: g.proxy_rate, ok: g.proxy_rate == null || g.proxy_rate < 10 }
  ]
})
const obsRows = computed(() => {
  const o = d.value.observe
  return [
    { name: '对账差异关闭率', target: '月末 100%', v: o.recon_close_rate },
    { name: '报废单据覆盖率（整件）', target: '≥ 95%', v: o.scrap_doc_rate },
    { name: '超期挂账清理率（本季）', target: '首季 ≥ 80%', v: o.overdue_clear_rate },
    { name: '审核 24 小时完成率', target: '—', v: o.approve_24h_rate },
    { name: '损耗率', target: '只观察', v: o.loss_rate }
  ]
})
onMounted(async () => { d.value = await api.get('/api/reports/dashboard') })
</script>
