<template>
  <div>
    <div class="page-head">
      <h2>对账与关账</h2>
      <span class="sub">锚点 = 单据号 + 行号 + 数量；差额必须为 0 才能关账（每月 3 日前完成上月关账）</span>
      <span class="grow" />
      <el-button @click="run"><el-icon><Refresh /></el-icon>立即对账</el-button>
    </div>
    <div v-if="sum" class="kpis">
      <div class="kpi g"><div class="l">已平单据</div><div class="v">{{ sum.balanced }}</div><div class="s">ERP 与明细完全一致（共 {{ sum.docs }} 张已过账单据）</div></div>
      <div class="kpi" :class="{ bad: sum.diff_docs }"><div class="l">差异单据</div><div class="v">{{ sum.diff_docs }}</div><div class="s">需认领处理{{ sum.no_doc_diffs ? ` · 另有 ${sum.no_doc_diffs} 条无单差异` : '' }}</div></div>
      <div class="kpi" :class="sum.diff_rate == null ? '' : sum.diff_rate < sum.target ? 'ok' : 'bad'"><div class="l">差异率</div><div class="v">{{ sum.diff_rate ?? '—' }}{{ sum.diff_rate != null ? '%' : '' }}</div><div class="s">目标 &lt; {{ sum.target }}%</div></div>
    </div>
    <div class="card">
      <h3>月份</h3>
      <el-table :data="periods" size="small">
        <el-table-column prop="period" label="月份" width="100" />
        <el-table-column label="差额不为 0 的差异" width="160"><template #default="{ row }"><b :style="{ color: row.open_diffs ? '#c0392b' : '#1c7f47' }">{{ row.open_diffs }}</b></template></el-table-column>
        <el-table-column label="状态"><template #default="{ row }">
          <el-tag v-if="row.closed" type="success" size="small">已关账 {{ row.closed_at }}</el-tag>
          <el-button v-else-if="hasRole('FINANCE')" size="small" type="primary" @click="close(row.period)">关账</el-button>
          <span v-else class="muted">未关账</span>
        </template></el-table-column>
      </el-table>
    </div>
    <div class="card">
      <h3>差异池
        <el-radio-group v-model="state" size="small" style="margin-left: 10px" @change="load">
          <el-radio-button value="">全部</el-radio-button><el-radio-button value="OPEN">待认领</el-radio-button>
          <el-radio-button value="CLAIMED">处理中</el-radio-button><el-radio-button value="RESOLVED">已平</el-radio-button>
        </el-radio-group>
      </h3>
      <el-table :data="diffs" size="small">
        <el-table-column prop="period" label="月份" width="80" />
        <el-table-column prop="kind_name" label="类型" width="150" />
        <el-table-column label="单据 / 行" width="190"><template #default="{ row }">
          {{ row.erp_doc_no || '—' }}<span v-if="row.erp_line_no"> 第 {{ row.erp_line_no }} 行</span>
          <router-link v-if="row.line_id" :to="`/line/${row.line_id}`" style="margin-left: 4px">明细</router-link>
        </template></el-table-column>
        <el-table-column prop="material" label="物料" />
        <el-table-column label="ERP / 明细 / 差额" width="160"><template #default="{ row }">{{ row.erp_qty }} / {{ row.detail_qty }} / <b :style="{ color: +row.diff ? '#c0392b' : '#1c7f47' }">{{ row.diff }}</b></template></el-table-column>
        <el-table-column prop="owner" label="认领人" width="80" />
        <el-table-column label="原因 / 消差" width="190"><template #default="{ row }">
          <span v-if="row.reason_code">{{ dictName('RECON_REASON', row.reason_code) }} · {{ METHODS[row.method] }} {{ row.method_ref || '' }}</span>
          <span v-else class="muted">{{ row.note || '' }}</span>
        </template></el-table-column>
        <el-table-column label="操作" width="150"><template #default="{ row }">
          <template v-if="row.state !== 'RESOLVED' && hasRole('KEEPER','FINANCE')">
            <el-button v-if="!row.claimed_at && hasRole('KEEPER')" size="small" @click="claim(row)">认领</el-button>
            <el-button size="small" type="primary" @click="openResolve(row)">消差</el-button>
          </template>
          <el-tag v-else-if="row.state === 'RESOLVED'" type="success" size="small">已平</el-tag>
        </template></el-table-column>
      </el-table>
    </div>
    <el-dialog v-model="dlg" title="消除差异" width="520px">
      <el-alert type="info" :closable="false" style="margin-bottom: 10px"
        title="中台侧差错：先在明细上发起冲销（IT 复核生效），填冲销事件号；ERP 侧差错：开金蝶调整单并经财务审核，填调整单号；漏登用补登，分摊不平用重新分摊。提交后系统重算，差额为 0 才能关闭。" />
      <el-form label-width="90px">
        <el-form-item label="原因码" required>
          <el-select v-model="rf.reason_code" style="width: 100%"><el-option v-for="d in dictOptions('RECON_REASON')" :key="d.code" :value="d.code" :label="`${d.code} ${d.name}`" /></el-select>
        </el-form-item>
        <el-form-item label="消差方式" required>
          <el-radio-group v-model="rf.method"><el-radio-button v-for="(n, k) in METHODS" :key="k" :value="k">{{ n }}</el-radio-button></el-radio-group>
        </el-form-item>
        <el-form-item v-if="rf.method === 'ADJUST' || rf.method === 'REVERSAL'" :label="rf.method === 'ADJUST' ? '调整单号' : '冲销事件号'" required>
          <el-input v-model="rf.ref" />
        </el-form-item>
        <el-form-item label="说明"><el-input v-model="rf.note" /></el-form-item>
      </el-form>
      <template #footer><el-button type="primary" @click="resolve">提交</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { hasRole } from '../store'
import { dictName, dictOptions } from '../utils'

const METHODS = { REVERSAL: '冲销', ADJUST: '调整单', BIND: '补登', ALLOC: '重新分摊' }
const periods = ref([])
const sum = ref(null)
const diffs = ref([])
const state = ref('')
const dlg = ref(false)
const cur = ref(null)
const rf = reactive({ reason_code: '', method: 'BIND', ref: '', note: '' })
async function load() {
  periods.value = await api.get('/api/recon/periods')
  diffs.value = await api.get(`/api/recon/diffs?state=${state.value}`)
  sum.value = await api.get('/api/recon/summary')
}
async function run() { const r = await api.post('/api/recon/run'); ElMessage.success(`已核对 ${r.checked} 项，新增差异 ${r.opened}`); load() }
async function claim(row) { await api.post(`/api/recon/diffs/${row.id}/claim`); load() }
function openResolve(row) { cur.value = row; Object.assign(rf, { reason_code: '', method: 'BIND', ref: '', note: '' }); dlg.value = true }
async function resolve() { await api.post(`/api/recon/diffs/${cur.value.id}/resolve`, rf); ElMessage.success('已平'); dlg.value = false; load() }
async function close(p) { await ElMessageBox.confirm(`确认关闭 ${p}？关账后该月不能再录单、不能冲销。`); await api.post(`/api/recon/close/${p}`); ElMessage.success('已关账'); load() }
onMounted(load)
</script>
