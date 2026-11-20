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
        <el-button type="primary" @click="openNew"><el-icon><Plus /></el-icon>录入单据</el-button>
        <el-upload :show-file-list="false" :http-request="doImport" accept=".xlsx"><el-button><el-icon><Upload /></el-icon>Excel 导入</el-button></el-upload>
      </template>
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
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { hasRole } from '../store'
import { DOC_TYPES, today } from '../utils'

const rows = ref([])
const type = ref('')
const q = ref('')
const dlg = ref(false)
const f = reactive({})
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
onMounted(load)
</script>
