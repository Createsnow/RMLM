<template>
  <div>
    <div class="page-head"><h2>主数据</h2><span class="sub">物料从金蝶只读同步（每天自动增量，也可手动）；项目暂用 Excel 导入；库位、工位、报废箱在此维护</span></div>
    <el-tabs v-model="tab" @tab-change="load">
      <el-tab-pane label="项目" name="project" />
      <el-tab-pane label="物料" name="material" />
      <el-tab-pane label="库位 / 工位 / 报废箱" name="location" />
    </el-tabs>
    <div class="actions-bar">
      <el-input v-model="q" placeholder="搜索" clearable style="width: 200px" @change="load" />
      <el-button type="primary" @click="openNew"><el-icon><Plus /></el-icon>新增</el-button>
      <el-upload v-if="tab !== 'location' && hasRole('KEEPER','ADMIN')" :show-file-list="false" :http-request="doImport" accept=".xlsx">
        <el-button><el-icon><Upload /></el-icon>Excel 导入</el-button>
      </el-upload>
      <template v-if="tab === 'material' && hasRole('KEEPER','ADMIN')">
        <el-button :loading="syncing" :disabled="!sync.configured" @click="doSync(false)"><el-icon><Refresh /></el-icon>从金蝶同步</el-button>
        <el-button link :disabled="!sync.configured || syncing" @click="doSync(true)">全量</el-button>
      </template>
      <el-checkbox v-if="tab === 'material'" v-model="inactive" @change="load">含已停用</el-checkbox>
      <span class="muted">{{ tab === 'project' ? '列：编码 | 名称 | 客户代码 | 已结项(是/否)' : tab === 'material' ? syncText : '' }}</span>
    </div>
    <div class="card" style="padding: 0">
      <el-table :data="rows" @row-click="edit">
        <el-table-column prop="code" label="编码" width="160" />
        <el-table-column prop="name" label="名称" />
        <el-table-column v-if="tab === 'project'" prop="customer_code" label="客户代码" width="100" />
        <el-table-column v-if="tab === 'project'" label="结项" width="70"><template #default="{ row }">{{ row.closed ? '是' : '' }}</template></el-table-column>
        <el-table-column v-if="tab === 'material'" prop="spec" label="规格" />
        <el-table-column v-if="tab === 'material'" prop="unit" label="单位" width="70" />
        <el-table-column v-if="tab === 'material'" prop="category" label="分组" width="120" />
        <el-table-column v-if="tab === 'material'" prop="unit_price" label="单价" width="90" />
        <el-table-column v-if="tab === 'material'" label="状态" width="80"><template #default="{ row }">
          <el-tag v-if="!row.active" type="info" size="small">已停用</el-tag>
        </template></el-table-column>
        <el-table-column v-if="tab === 'location'" label="类型" width="100"><template #default="{ row }">{{ KINDS[row.kind] }}</template></el-table-column>
      </el-table>
    </div>
    <el-dialog v-model="dlg" title="编辑" width="460px">
      <el-form label-width="80px">
        <el-form-item label="编码"><el-input v-model="f.code" :disabled="!!f.id" /></el-form-item>
        <el-form-item label="名称"><el-input v-model="f.name" /></el-form-item>
        <template v-if="tab === 'project'">
          <el-form-item label="客户代码"><el-input v-model="f.customer_code" /></el-form-item>
          <el-form-item label="已结项"><el-switch v-model="f.closed" /></el-form-item>
        </template>
        <template v-if="tab === 'material'">
          <el-form-item label="规格"><el-input v-model="f.spec" /></el-form-item>
          <el-form-item label="单位"><el-input v-model="f.unit" /></el-form-item>
          <el-form-item label="单价"><el-input-number v-model="f.unit_price" :precision="4" :min="0" /></el-form-item>
        </template>
        <template v-if="tab === 'location'">
          <el-form-item label="类型"><el-select v-model="f.kind"><el-option v-for="(n, k) in KINDS" :key="k" :value="k" :label="n" /></el-select></el-form-item>
          <el-form-item label="启用"><el-switch v-model="f.active" /></el-form-item>
        </template>
      </el-form>
      <template #footer><el-button type="primary" @click="save">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { hasRole } from '../store'

const KINDS = { STOCK: '库位', STATION: '工位', SCRAP_BOX: '报废箱' }
const tab = ref('project')
const q = ref('')
const rows = ref([])
const dlg = ref(false)
const f = reactive({})
const URL = { project: '/api/projects', material: '/api/materials', location: '/api/locations' }
const inactive = ref(false)
const sync = ref({ configured: false })
const syncing = ref(false)
const syncText = computed(() => {
  if (!sync.value.configured) return '未配置金蝶接口，可先用 Excel 导入。列：编码 | 名称 | 规格 | 单位 | 单价 | 类别'
  const r = sync.value.last_result
  return sync.value.last_run_at ? `最近同步 ${sync.value.last_run_at}（${r ? `新增 ${r.created}、更新 ${r.updated}、停用 ${r.disabled}` : ''}）` : '尚未同步'
})
async function load() {
  const qs = encodeURIComponent(q.value)
  rows.value = await api.get(tab.value === 'project' ? `/api/projects?include_closed=true&q=${qs}` : tab.value === 'material' ? `/api/materials?q=${qs}&include_inactive=${inactive.value}` : '/api/locations')
  if (tab.value === 'material') sync.value = await api.get('/api/master/sync/status')
}
async function doSync(full) {
  syncing.value = true
  try {
    const r = await api.post(`/api/master/sync/material?full=${full}`)
    ElMessage.success(`同步完成：拉取 ${r.fetched} 条，新增 ${r.created}，更新 ${r.updated}，停用 ${r.disabled}`)
    load()
  } finally { syncing.value = false }
}
function openNew() { Object.keys(f).forEach(k => delete f[k]); Object.assign(f, { kind: 'STOCK', active: true, closed: false, unit: '个' }); dlg.value = true }
function edit(row) { Object.keys(f).forEach(k => delete f[k]); Object.assign(f, row, { unit_price: row.unit_price != null ? +row.unit_price : null, active: true }); dlg.value = true }
async function save() { await api.post(URL[tab.value], f); ElMessage.success('已保存'); dlg.value = false; load() }
async function doImport({ file }) { const r = await api.upload(`/api/master/import/${tab.value}`, file); ElMessage.success(`已导入 ${r.count} 条`); load() }
onMounted(load)
</script>
