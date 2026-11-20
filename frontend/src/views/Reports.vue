<template>
  <div>
    <div class="page-head"><h2>报表</h2><span class="sub">数据范围按您的权限</span></div>
    <el-tabs v-model="tab" @tab-change="load">
      <el-tab-pane label="周报挂账" name="weekly">
        <div v-if="weekly">
          <div v-for="(t, k) in W" :key="k" class="card">
            <h3>{{ t }}（{{ weekly[k].length }}）</h3>
            <el-table v-if="k !== 'level3'" :data="weekly[k]" size="small" @row-click="r => $router.push(`/line/${r.id}`)">
              <el-table-column label="物料码" width="130"><template #default="{ row }"><span class="mono">{{ row.code }}</span></template></el-table-column>
              <el-table-column prop="material" label="物料" />
              <el-table-column prop="project" label="项目" width="120" />
              <el-table-column prop="holder" label="持有人" width="90" />
              <el-table-column label="结存" width="100"><template #default="{ row }">{{ row.balance }} {{ row.unit }}</template></el-table-column>
              <el-table-column prop="due_date" label="归还日" width="110" />
            </el-table>
            <el-table v-else :data="weekly.level3" size="small">
              <el-table-column prop="title" label="事项" />
              <el-table-column prop="owner" label="责任人" width="100" />
              <el-table-column prop="created_at" label="产生时间" width="160" />
            </el-table>
          </div>
        </div>
      </el-tab-pane>
      <el-tab-pane label="项目视图" name="projects">
        <div class="card" style="padding: 0">
          <el-table :data="projects">
            <el-table-column prop="project" label="项目" width="140" />
            <el-table-column prop="name" label="名称" />
            <el-table-column label="结项" width="70"><template #default="{ row }">{{ row.closed ? '是' : '' }}</template></el-table-column>
            <el-table-column prop="lines" label="行数" width="70" />
            <el-table-column prop="active" label="在制" width="70" />
            <el-table-column label="领用金额" width="120" align="right"><template #default="{ row }">{{ fmtMoney(row.issued_amount) }}</template></el-table-column>
            <el-table-column label="耗用金额（含损耗）" width="150" align="right"><template #default="{ row }">{{ fmtMoney(row.consumed_amount) }}</template></el-table-column>
            <el-table-column label="结存金额" width="120" align="right"><template #default="{ row }">{{ fmtMoney(row.balance_amount) }}</template></el-table-column>
          </el-table>
        </div>
      </el-tab-pane>
      <el-tab-pane label="损耗" name="loss">
        <el-row v-if="loss" :gutter="14">
          <el-col v-for="(t, k) in { by_user: '按人', by_material: '按物料' }" :key="k" :md="12" :xs="24">
            <div class="card"><h3>{{ t }}（只观察，不拦截）</h3>
              <el-table :data="loss[k]" size="small">
                <el-table-column prop="name" label="名称" />
                <el-table-column prop="issued" label="领用" width="90" />
                <el-table-column prop="loss" label="损耗" width="90" />
                <el-table-column label="损耗率" width="90"><template #default="{ row }">{{ row.rate ?? '—' }}{{ row.rate != null ? '%' : '' }}</template></el-table-column>
              </el-table>
            </div>
          </el-col>
        </el-row>
      </el-tab-pane>
      <el-tab-pane v-if="hasRole('KEEPER','FINANCE','ADMIN','DEPT_HEAD','VP')" label="月度清单" name="monthly">
        <el-date-picker v-model="period" type="month" value-format="YYYY-MM" @change="load" style="margin-bottom: 12px" />
        <div v-if="monthly">
          <div class="card"><h3>R06「其他」原因申请（月度复盘逐条清理）</h3>
            <el-table :data="monthly.r06" size="small"><el-table-column prop="no" label="申请号" width="170" /><el-table-column prop="user" label="使用人" width="90" /><el-table-column prop="remark" label="说明" /><el-table-column prop="created_at" label="时间" width="160" /></el-table>
          </div>
          <div class="card"><h3>自领自开（月度抽查）</h3>
            <el-table :data="monthly.self_issue" size="small"><el-table-column prop="code" label="物料码" /><el-table-column prop="user" label="领料专员" /><el-table-column prop="created_at" label="时间" /></el-table>
          </div>
        </div>
      </el-tab-pane>
      <el-tab-pane label="季度清单" name="quarterly">
        <div v-if="quarterly">
          <div class="card"><h3>{{ quarterly.quarter }} 盘点抽样（每项目抽 20% 在制行，至少 1 行；差异不追责但须修正数据并记录原因）</h3>
            <el-table :data="quarterly.stocktake_sample" size="small"><el-table-column prop="project" label="项目" /><el-table-column prop="code" label="物料码" /><el-table-column prop="balance" label="系统结存" /><el-table-column label="实盘数量"><template #default>______</template></el-table-column></el-table>
          </div>
          <div class="card"><h3>字典使用复盘（本季零使用或低于 1% 的枚举考虑合并 / 停用）</h3>
            <el-table :data="usageRows" size="small"><el-table-column prop="code" label="编码" /><el-table-column prop="name" label="名称" /><el-table-column prop="n" label="使用次数" /></el-table>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { hasRole, store } from '../store'
import { fmtMoney } from '../utils'

const W = { overdue: '超期未结案', urgent: '紧急领用超 24 小时未补', hold_30d: '暂存超 30 天', doc_pending: '待单据处置', level3: '三级挂账（提交月度经营会）' }
const tab = ref('weekly')
const weekly = ref(null)
const projects = ref([])
const loss = ref(null)
const monthly = ref(null)
const quarterly = ref(null)
const d = new Date(); d.setMonth(d.getMonth() - 1)
const period = ref(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`)
const usageRows = computed(() => {
  if (!quarterly.value) return []
  const out = []
  for (const t of ['DEST', 'REASON', 'LOSS_REASON', 'SCRAP_REASON']) for (const it of store.dicts[t] || []) out.push({ code: it.code, name: it.name, n: quarterly.value.dict_usage[it.code] || 0 })
  return out.sort((a, b) => a.n - b.n)
})
async function load() {
  if (tab.value === 'weekly') weekly.value = await api.get('/api/reports/weekly')
  if (tab.value === 'projects') projects.value = await api.get('/api/reports/projects')
  if (tab.value === 'loss') loss.value = await api.get('/api/reports/loss')
  if (tab.value === 'monthly') monthly.value = await api.get(`/api/reports/monthly?period=${period.value}`)
  if (tab.value === 'quarterly') quarterly.value = await api.get('/api/reports/quarterly')
}
onMounted(load)
</script>
