<template>
  <div>
    <div class="page-head">
      <h2>ERP 单据</h2>
      <span class="sub">金蝶只读镜像：手工录入或 Excel 导入金蝶导出的单据，中台不回写金蝶</span>
      <span class="grow" />
      <el-select v-model="type" clearable placeholder="全部类型" style="width: 140px" @change="load">
        <el-option v-for="(n, k) in DOC_TYPES" :key="k" :value="k" :label="n" />
      </el-select>
      <el-input v-model="q" placeholder="单据号 / 备注" clearable style="width: 180px" @change="load" />
      <template v-if="hasRole('KEEPER', 'ADMIN')">
        <el-dropdown split-button :disabled="syncing" @click="syncK3(false)" @command="syncK3(true)">
          {{ syncing ? '同步中…' : '从金蝶同步领料单' }}
          <template #dropdown><el-dropdown-menu><el-dropdown-item command="full">全量同步（忽略增量水位，较慢）</el-dropdown-item></el-dropdown-menu></template>
        </el-dropdown>
        <el-button type="primary" @click="openNew"><el-icon><Plus /></el-icon>录入单据</el-button>
        <el-upload :show-file-list="false" :http-request="doImport" accept=".xlsx"><el-button><el-icon><Upload /></el-icon>Excel 导入</el-button></el-upload>
      </template>
    </div>
    <div v-if="sync.configured && hasRole('KEEPER', 'ADMIN')" class="card sync-bar">
      <span>金蝶自动同步：</span>
      <el-tag v-if="!sync.scheduler" type="danger" size="small">定时任务未启动（.env RMLM_SCHEDULER=0）</el-tag>
      <el-tag v-else-if="sync.auto?.enabled" type="success" size="small">每 {{ intervalText(sync.auto.interval) }}</el-tag>
      <el-tag v-else type="info" size="small">已关闭</el-tag>
      <span class="muted">最近同步 {{ sync.last_run_at || '尚未同步' }}</span>
      <span v-if="sync.scheduler && sync.auto?.next_run_at" class="muted">下次约 {{ sync.auto.next_run_at }} 后</span>
      <span v-if="!sync.last_run_at && sync.start_date" class="muted">首次同步从 {{ sync.start_date }} 起</span>
      <span class="grow" />
      <el-button v-if="hasRole('ADMIN')" size="small" @click="openAuto">自动同步设置</el-button>
    </div>
    <div class="card" style="padding: 0">
      <el-table :data="rows" row-key="id">
        <el-table-column type="expand"><template #default="{ row }">
          <el-table :data="row.lines" size="small" style="margin: 0 20px">
            <el-table-column prop="line_no" label="行" width="50" />
            <el-table-column label="物料"><template #default="{ row: l }">{{ l.material_code }} {{ l.material }}</template></el-table-column>
            <el-table-column label="数量" width="100"><template #default="{ row: l }">{{ l.qty }} {{ l.unit }}</template></el-table-column>
            <el-table-column prop="unit_price" label="单价" width="90" />
            <el-table-column prop="project_code" label="项目号" width="120" />
            <el-table-column v-if="row.doc_type === 'TZ'" prop="ref_line_no" label="原单行" width="70" />
            <el-table-column label="领用行" width="120"><template #default="{ row: l }"><span class="mono">{{ l.bound_line || '' }}</span></template></el-table-column>
          </el-table>
        </template></el-table-column>
        <el-table-column prop="no" label="单据号" width="180" />
        <el-table-column label="类型" width="110"><template #default="{ row }">{{ DOC_TYPES[row.doc_type] }}</template></el-table-column>
        <el-table-column prop="doc_date" label="日期" width="110" />
        <el-table-column label="过账" width="70"><template #default="{ row }"><el-tag :type="row.posted ? 'success' : 'info'" size="small">{{ row.posted ? '已过账' : '未过账' }}</el-tag></template></el-table-column>
        <el-table-column prop="remark" label="备注" show-overflow-tooltip />
        <el-table-column label="原单" width="150"><template #default="{ row }">{{ row.ref_doc_no || '' }}</template></el-table-column>
        <el-table-column label="状态 / 操作" width="250"><template #default="{ row }">
          <el-button v-if="hasRole('KEEPER','ADMIN')" size="small" text @click="togglePosted(row)">{{ row.posted ? '改为未过账' : '标记已过账' }}</el-button>
          <template v-if="row.doc_type === 'TZ'">
            <el-tag v-if="row.finance_approved" type="success" size="small">财务已审</el-tag>
            <el-button v-else-if="hasRole('FINANCE')" size="small" type="primary" @click="finApprove(row)">财务审核</el-button>
            <span v-else class="muted">待财务审核</span>
          </template>
          <template v-if="['TL','BF'].includes(row.doc_type)">
            <el-tag v-if="row.received" type="success" size="small">实物已接收</el-tag>
            <el-button v-else-if="hasRole('WAREHOUSE')" size="small" type="primary" @click="receive(row)">确认实物</el-button>
            <span v-else class="muted">待仓库确认</span>
          </template>
        </template></el-table-column>
      </el-table>
    </div>
    <div class="muted">Excel 列：单据号 | 类型(LL/AB/TL/BF/TZ) | 日期 | 已过账(是/否) | 备注 | 原单号 | 行号 | 物料编码 | 数量 | 单价 | 项目号 | 原单行号（每行一条明细，同单据号自动合并）</div>

    <el-dialog v-model="dlg" title="录入金蝶单据" width="720px">
      <el-form label-width="90px">
        <el-row :gutter="10">
          <el-col :span="12"><el-form-item label="单据号" required><el-input v-model="f.no" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="类型" required>
            <el-select v-model="f.doc_type" style="width: 100%"><el-option v-for="(n, k) in DOC_TYPES" :key="k" :value="k" :label="n" /></el-select>
          </el-form-item></el-col>
          <el-col :span="12"><el-form-item label="日期"><el-date-picker v-model="f.doc_date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="已过账"><el-switch v-model="f.posted" /></el-form-item></el-col>
          <el-col :span="24"><el-form-item label="备注"><el-input v-model="f.remark" placeholder="领料单请填中台申请号，如 REQ-20261102-0001" /></el-form-item></el-col>
          <el-col v-if="f.doc_type === 'TZ'" :span="24"><el-form-item label="被调整原单" required><el-input v-model="f.ref_doc_no" /></el-form-item></el-col>
        </el-row>
        <el-table :data="f.lines" size="small">
          <el-table-column label="行" width="50"><template #default="{ $index }">{{ $index + 1 }}</template></el-table-column>
          <el-table-column label="物料编码"><template #default="{ row }"><el-input v-model="row.material_code" size="small" /></template></el-table-column>
          <el-table-column label="数量" width="110"><template #default="{ row }"><el-input v-model="row.qty" size="small" /></template></el-table-column>
          <el-table-column label="单价" width="100"><template #default="{ row }"><el-input v-model="row.unit_price" size="small" /></template></el-table-column>
          <el-table-column label="项目号" width="120"><template #default="{ row }"><el-input v-model="row.project_code" size="small" /></template></el-table-column>
          <el-table-column v-if="f.doc_type === 'TZ'" label="原单行" width="80"><template #default="{ row }"><el-input v-model="row.ref_line_no" size="small" /></template></el-table-column>
        </el-table>
        <el-button size="small" style="margin-top: 6px" @click="f.lines.push({ material_code: '', qty: '' })">加一行</el-button>
      </el-form>
      <template #footer><el-button type="primary" @click="save">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="autoDlg" title="金蝶领料单自动同步设置" width="460px">
      <el-form label-width="90px">
        <el-form-item label="自动同步"><el-switch v-model="auto.enabled" active-text="开启" inactive-text="关闭" /></el-form-item>
        <el-form-item label="同步间隔">
          <el-select v-model="auto.interval" :disabled="!auto.enabled" style="width: 100%">
            <el-option v-for="m in sync.auto?.options || []" :key="m" :value="m" :label="`每 ${intervalText(m)}`" />
          </el-select>
        </el-form-item>
      </el-form>
      <div class="muted">按上次同步（含手动同步）时间加间隔，在之后的第一次定时巡检（每 15 分钟一次）时拉取；关闭后只能手动同步。
        首次同步只拉 .env 里 K3_SYNC_START_DATE（默认今天往前 3 个月）之后的单据。</div>
      <template #footer><el-button type="primary" @click="saveAuto">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { hasRole } from '../store'
import { DOC_TYPES, today } from '../utils'
import { useRefresh } from '../useRefresh'

const rows = ref([])
const type = ref('')
const q = ref('')
const dlg = ref(false)
const f = reactive({})
const syncing = ref(false)
const sync = ref({ configured: false })
const autoDlg = ref(false)
const auto = reactive({ enabled: true, interval: 15 })
function intervalText(m) { return m % 60 ? `${m} 分钟` : `${m / 60} 小时` }
async function loadSync() { if (hasRole('KEEPER', 'ADMIN')) sync.value = await api.get('/api/erp/docs/sync/status') }
function openAuto() { Object.assign(auto, { enabled: sync.value.auto.enabled, interval: sync.value.auto.interval }); autoDlg.value = true }
async function saveAuto() {
  sync.value = await api.put('/api/erp/docs/sync/settings', { enabled: auto.enabled, interval: auto.interval })
  ElMessage.success('已保存')
  autoDlg.value = false
}
async function syncK3(full) {
  if (full) {
    try {
      await ElMessageBox.confirm('全量同步会忽略增量水位，从金蝶重新拉取起始日期（.env 的 K3_SYNC_START_DATE，默认今天往前 3 个月）之后的研发领料单（研发测试、客供领料其他出库单），用于新增单据类型或补漏，耗时较长。继续？', '全量同步', { type: 'warning' })
    } catch { return }
  }
  syncing.value = true
  try {
    const r = await api.post(`/api/erp/docs/sync${full ? '?full=true' : ''}`)
    ElMessage[r.errors?.length ? 'warning' : 'success'](`金蝶领料单：取回 ${r.fetched}，新增 ${r.created}，更新 ${r.updated}，自动生成领用行 ${r.auto_bound}${r.errors?.length ? `，跳过 ${r.errors.length} 张（已派待办）` : ''}`)
    load()
    loadSync()
  } finally { syncing.value = false }
}
async function load() { rows.value = await api.get(`/api/erp/docs?doc_type=${type.value || ''}&q=${encodeURIComponent(q.value)}`) }
function openNew() {
  Object.assign(f, { no: '', doc_type: 'LL', doc_date: today(), posted: true, remark: '', ref_doc_no: '', lines: [{ material_code: '', qty: '' }] })
  dlg.value = true
}
async function save() {
  const body = { ...f, ref_doc_no: f.ref_doc_no || null, lines: f.lines.filter(l => l.material_code).map((l, i) => ({
    line_no: i + 1, material_code: l.material_code, qty: +l.qty, unit_price: l.unit_price ? +l.unit_price : null,
    project_code: l.project_code || null, ref_line_no: l.ref_line_no ? +l.ref_line_no : null })) }
  await api.post('/api/erp/docs', body)
  ElMessage.success('已保存')
  dlg.value = false
  load()
}
async function doImport({ file }) { const r = await api.upload('/api/erp/docs/import', file); ElMessage.success(`已导入 ${r.count} 张单据`); load() }
async function togglePosted(row) { await api.post(`/api/erp/docs/${row.id}/posted?posted=${!row.posted}`); load() }
async function finApprove(row) { await api.post(`/api/erp/docs/${row.id}/finance-approve`); ElMessage.success('已审核'); load() }
async function receive(row) { await api.post(`/api/erp/docs/${row.id}/receive`); ElMessage.success('已确认'); load() }
useRefresh(() => { load(); loadSync() })
</script>

<style scoped>
.sync-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 10px 16px; font-size: 13px; }
.sync-bar .grow { flex: 1; }
</style>
