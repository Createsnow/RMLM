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
              <el-table-column v-if="k === 'lab_pending'" prop="sent_at" label="送实验时间" width="160" />
              <el-table-column v-else prop="due_date" label="归还日" width="110" />
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
          <div class="card"><h3>字典使用复盘（整季零使用或使用率低于 1% 的枚举建议合并 / 停用，走字典变更）</h3>
            <el-table :data="quarterly.dict_review" size="small">
              <el-table-column label="字典" width="120"><template #default="{ row }">{{ DICT_NAMES[row.dict_type] }}</template></el-table-column>
              <el-table-column prop="code" label="编码" width="80" /><el-table-column prop="name" label="名称" />
              <el-table-column prop="count" label="本季使用" width="100" />
              <el-table-column label="使用率" width="100"><template #default="{ row }">{{ row.pct ?? '—' }}{{ row.pct != null ? '%' : '' }}</template></el-table-column>
              <el-table-column label="建议" width="130"><template #default="{ row }"><el-tag v-if="row.suggest" type="warning" size="small">建议合并 / 停用</el-tag></template></el-table-column>
            </el-table>
          </div>
        </div>
      </el-tab-pane>
      <el-tab-pane v-if="hasRole('KEEPER','FINANCE','ADMIN','DEPT_HEAD','VP')" label="双轨抽查" name="parallel">
        <div v-if="par">
          <div class="page-head" style="margin-bottom: 10px">
            <el-date-picker v-model="weekDate" type="week" format="YYYY [第] ww [周]" value-format="YYYY-MM-DD" @change="loadPar" />
            <span class="sub">第 1 月并行期：每周从当周签收的行里抽 {{ par.summary.sample_size }} 单与旧表比对，连续两周一致率 ≥ {{ par.summary.target }}% 即可进入收口</span>
            <span class="grow" />
            <el-tag :type="par.summary.exit_ok ? 'success' : 'info'">{{ par.summary.exit_ok ? '已满足退出条件，可进入收口' : '未满足退出条件' }}</el-tag>
            <el-button v-if="hasRole('KEEPER','ADMIN') && !par.rows.length" type="primary" @click="samplePar">抽取 {{ par.week }} 样本</el-button>
          </div>
          <div class="card"><h3>{{ par.week }} 样本（{{ par.rows.length }}）</h3>
            <el-table :data="par.rows" size="small" empty-text="本周尚未抽样">
              <el-table-column label="物料码" width="130"><template #default="{ row }"><router-link :to="`/line/${row.line_id}`" class="mono">{{ row.code }}</router-link></template></el-table-column>
              <el-table-column label="物料"><template #default="{ row }">{{ row.material_code }} {{ row.material }}</template></el-table-column>
              <el-table-column label="数量" width="90"><template #default="{ row }">{{ row.qty }} {{ row.unit }}</template></el-table-column>
              <el-table-column prop="project" label="项目" width="110" />
              <el-table-column prop="user" label="使用人" width="80" />
              <el-table-column label="与旧表比对" width="300"><template #default="{ row }">
                <template v-if="hasRole('KEEPER','ADMIN')">
                  <el-radio-group v-model="row.result" size="small" @change="v => markPar(row, v)">
                    <el-radio-button value="MATCH">一致</el-radio-button><el-radio-button value="DIFF">不一致</el-radio-button>
                  </el-radio-group>
                  <span v-if="row.note" class="muted" style="margin-left: 6px">{{ row.note }}</span>
                </template>
                <span v-else>{{ { MATCH: '一致', DIFF: '不一致' }[row.result] || '未比对' }} {{ row.note || '' }}</span>
              </template></el-table-column>
            </el-table>
          </div>
          <div class="card"><h3>各周一致率</h3>
            <el-table :data="par.summary.weeks" size="small">
              <el-table-column prop="week" label="周" width="110" /><el-table-column prop="sampled" label="样本" width="80" />
              <el-table-column prop="checked" label="已比对" width="80" /><el-table-column prop="match" label="一致" width="80" />
              <el-table-column label="一致率" width="100"><template #default="{ row }"><b :style="{ color: row.rate == null ? '' : row.pass ? '#1c7f47' : '#c0392b' }">{{ row.rate ?? '—' }}{{ row.rate != null ? '%' : '' }}</b></template></el-table-column>
              <el-table-column label="状态"><template #default="{ row }">{{ !row.done ? '比对中' : row.pass ? '达标' : '未达标' }}</template></el-table-column>
            </el-table>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { hasRole } from '../store'
import { fmtMoney } from '../utils'
import { useRefresh } from '../useRefresh'

const W = { overdue: '超期未结案', urgent: '紧急领用超 24 小时未补', hold_30d: '暂存超 30 天', doc_pending: '待单据处置', lab_pending: 'D02 实验结论超 30 天未回填', level3: '三级挂账（提交月度经营会）' }
const tab = ref('weekly')
const weekly = ref(null)
const projects = ref([])
const loss = ref(null)
const monthly = ref(null)
const quarterly = ref(null)
const d = new Date(); d.setMonth(d.getMonth() - 1)
const period = ref(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`)
const DICT_NAMES = { DEST: '去向', REASON: '领料原因', LOSS_REASON: '损耗原因', SCRAP_REASON: '报废原因', RECON_REASON: '差异原因码' }
const par = ref(null)
const weekDate = ref(null)
function isoWeek(dateStr) {
  const d = dateStr ? new Date(dateStr) : new Date()
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()))
  const day = t.getUTCDay() || 7
  t.setUTCDate(t.getUTCDate() + 4 - day)
  const y = t.getUTCFullYear()
  const w = Math.ceil(((t - Date.UTC(y, 0, 1)) / 86400000 + 1) / 7)
  return `${y}-W${String(w).padStart(2, '0')}`
}
async function loadPar() { par.value = await api.get(`/api/parallel?week=${isoWeek(weekDate.value)}`) }
async function samplePar() { par.value = await api.post(`/api/parallel/sample?week=${par.value.week}`); ElMessage.success(`已抽取 ${par.value.rows.length} 单`) }
async function markPar(row, v) {
  let note = null
  if (v === 'DIFF') {
    try { note = (await ElMessageBox.prompt('写明与旧表差在哪里（数量、项目、持有人等）', '不一致')).value } catch { row.result = null; return }
  }
  try {
    Object.assign(row, await api.post(`/api/parallel/${row.id}`, { result: v, note }))
  } catch { row.result = null; return }
  par.value.summary = (await api.get(`/api/parallel?week=${par.value.week}`)).summary
}
async function load() {
  if (tab.value === 'weekly') weekly.value = await api.get('/api/reports/weekly')
  if (tab.value === 'projects') projects.value = await api.get('/api/reports/projects')
  if (tab.value === 'loss') loss.value = await api.get('/api/reports/loss')
  if (tab.value === 'monthly') monthly.value = await api.get(`/api/reports/monthly?period=${period.value}`)
  if (tab.value === 'quarterly') quarterly.value = await api.get('/api/reports/quarterly')
  if (tab.value === 'parallel') await loadPar()
}
useRefresh(load)
</script>
