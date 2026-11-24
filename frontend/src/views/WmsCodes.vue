<template>
  <div>
    <div class="page-head">
      <h2>仓储码</h2>
      <span class="sub">物料码由仓储系统统一赋码，本系统只读赋码清单：签收时扫码绑定，拆分时扫仓储预发的空白码</span>
      <span class="grow" />
      <el-button v-if="st" :disabled="!st.enabled" :loading="syncing" @click="doSync"><el-icon><Refresh /></el-icon>从仓储同步</el-button>
      <el-upload :show-file-list="false" accept=".xlsx" :http-request="doImport">
        <el-button type="primary"><el-icon><Upload /></el-icon>Excel 导入赋码清单</el-button>
      </el-upload>
    </div>
    <div v-if="st" class="kpis">
      <div class="kpi"><div class="l">清单总数</div><div class="v">{{ st.total }}</div><div class="s">{{ st.enabled ? `接口同步 · 最近 ${st.last_run_at || '尚未同步'}` : '未配置接口，用 Excel 导入' }}</div></div>
      <div class="kpi b"><div class="l">未绑定</div><div class="v">{{ st.free }}</div><div class="s">B1 已赋码，等待签收或拆分</div></div>
      <div class="kpi" :class="st.blank_free < 10 ? 'bad' : 'g'"><div class="l">可用空白码</div><div class="v">{{ st.blank_free }}</div><div class="s">拆分换码用，不足时向仓库申领</div></div>
    </div>
    <div class="actions-bar">
      <el-input v-model="q" placeholder="仓储码 / 物料编码" clearable style="width: 220px" @change="load" />
      <el-radio-group v-model="state" @change="load">
        <el-radio-button value="">全部</el-radio-button><el-radio-button value="free">未绑定</el-radio-button>
        <el-radio-button value="blank">空白码</el-radio-button><el-radio-button value="bound">已绑定</el-radio-button>
        <el-radio-button value="void">已作废</el-radio-button>
      </el-radio-group>
      <span class="muted">Excel 列：仓储码 | 物料编码（空白码留空）| 数量 | 单位 | 批次 | 作废(是/否)</span>
    </div>
    <div class="card" style="padding: 0">
      <el-table :data="rows" size="small">
        <el-table-column label="仓储码" width="170"><template #default="{ row }"><span class="mono">{{ row.code }}</span></template></el-table-column>
        <el-table-column label="物料编码" width="140"><template #default="{ row }">{{ row.material_code || '空白码' }}</template></el-table-column>
        <el-table-column label="数量" width="100"><template #default="{ row }">{{ row.qty ?? '—' }} {{ row.unit || '' }}</template></el-table-column>
        <el-table-column prop="batch" label="批次" width="120" />
        <el-table-column label="状态" width="100"><template #default="{ row }">
          <el-tag size="small" :type="{ B1: 'primary', BOUND: 'success', VOID: 'info' }[row.state]">{{ { B1: '已赋码', BOUND: '已绑定', VOID: '已作废' }[row.state] }}</el-tag>
        </template></el-table-column>
        <el-table-column label="领用行"><template #default="{ row }"><router-link v-if="row.line_id" :to="`/line/${row.line_id}`">查看</router-link><span v-if="row.bound_at" class="muted"> {{ row.bound_at }}</span></template></el-table-column>
        <el-table-column label="来源" width="80"><template #default="{ row }">{{ row.source === 'API' ? '接口' : '导入' }}</template></el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'

const rows = ref([])
const st = ref(null)
const q = ref('')
const state = ref('free')
const syncing = ref(false)
async function load() {
  const r = await api.get(`/api/wms/codes?q=${encodeURIComponent(q.value)}&state=${state.value}`)
  rows.value = r.rows; st.value = r.status
}
async function doImport({ file }) {
  const r = await api.upload('/api/wms/codes/import', file)
  ElMessage.success(`新增 ${r.created}、更新 ${r.updated}`)
  load()
}
async function doSync() {
  syncing.value = true
  try { const r = await api.post('/api/wms/sync'); ElMessage.success(`新增 ${r.created}、更新 ${r.updated}`); load() } finally { syncing.value = false }
}
onMounted(load)
</script>
