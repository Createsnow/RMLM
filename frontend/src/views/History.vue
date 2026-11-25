<template>
  <div>
    <div class="page-head">
      <h2>历史基线</h2>
      <span class="sub">旧台账有效明细导入为只读基线，只用于查询与总量核对；不生成领用行、不进差异池、不计入验收指标</span>
      <span class="grow" />
      <el-upload v-if="hasRole('KEEPER', 'ADMIN')" :show-file-list="false" accept=".xlsx" :http-request="upload">
        <el-button type="primary"><el-icon><Upload /></el-icon>导入旧台账 Excel</el-button>
      </el-upload>
    </div>

    <el-alert v-if="last" type="success" :closable="false" style="margin-bottom: 12px"
              :title="`批次 ${last.batch}：导入 ${last.count} 行（跳过无效 ${last.skipped} 行）；去向命中规则 ${last.mapped} 行、未命中 ${last.unmapped} 行；项目号按出库单回填 ${last.backfilled} 行、挂「${virtualProject}」${last.virtual} 行`" />

    <div class="card">
      <h3>导入格式与转换规则</h3>
      <div class="muted" style="margin-bottom: 8px">第一行为表头，列顺序：{{ columns.join(' | ') }}。每行至少要有领料单号或物料编码，以及领用数量。</div>
      <el-table :data="rules" size="small">
        <el-table-column prop="name" label="规则" width="150" />
        <el-table-column label="关键词" width="240"><template #default="{ row }">{{ row.keywords.join(' / ') || '—' }}</template></el-table-column>
        <el-table-column label="归一为" width="130"><template #default="{ row }">{{ row.dest ? dictName('DEST', row.dest) : '—' }}</template></el-table-column>
        <el-table-column prop="handling" label="处理方式" />
      </el-table>
    </div>

    <el-tabs v-model="tab" @tab-change="load">
      <el-tab-pane label="基线明细" name="rows">
        <div class="filters">
          <el-input v-model="q" placeholder="单号 / 物料 / 领用人" clearable style="width: 200px" @change="load" />
          <el-select v-model="dest" clearable placeholder="去向" style="width: 160px" @change="load">
            <el-option value="NONE" label="未命中规则" />
            <el-option v-for="d in dictOptions('DEST')" :key="d.code" :value="d.code" :label="`${d.code} ${d.name}`" />
          </el-select>
          <el-select v-model="batch" clearable placeholder="导入批次" style="width: 220px" @change="load">
            <el-option v-for="b in batches" :key="b.batch" :value="b.batch" :label="`${b.batch}（${b.count} 行）`" />
          </el-select>
          <span class="muted">共 {{ total }} 行{{ total > rows.length ? `，显示最近 ${rows.length} 行` : '' }}</span>
        </div>
        <div class="card" style="padding: 0">
          <el-table :data="rows" size="small">
            <el-table-column label="领料单号" width="170"><template #default="{ row }"><span class="mono">{{ row.doc_no || '—' }}</span><div class="muted">{{ row.doc_date }}</div></template></el-table-column>
            <el-table-column label="项目" width="140"><template #default="{ row }">{{ row.project_code }}<div class="muted">{{ SRC[row.project_source] }}</div></template></el-table-column>
            <el-table-column label="物料" min-width="160"><template #default="{ row }">{{ row.material_code }} {{ row.material_name }}<div class="muted">{{ row.spec }}</div></template></el-table-column>
            <el-table-column label="数量" width="90" align="right"><template #default="{ row }">{{ row.qty }} {{ row.unit }}</template></el-table-column>
            <el-table-column prop="user_name" label="领用人" width="80" />
            <el-table-column label="原文 → 去向" min-width="200"><template #default="{ row }">
              <span class="muted">{{ row.dest_raw || '—' }}</span> →
              <el-tag v-if="row.dest_code" size="small">{{ dictName('DEST', row.dest_code) }}</el-tag>
              <el-tag v-else size="small" type="warning">未命中</el-tag>
              <div v-if="row.location_text || row.holder_name" class="muted">位置 {{ row.location_text || '未编码位置' }} · 持有人 {{ row.holder_name || '—' }}</div>
            </template></el-table-column>
          </el-table>
        </div>
      </el-tab-pane>
      <el-tab-pane label="总量核对" name="check">
        <div v-if="chk" class="kpis" style="grid-template-columns: repeat(3, minmax(0, 1fr))">
          <div class="kpi g"><div class="l">与 ERP 一致</div><div class="v">{{ chk.summary.OK }}</div><div class="s">单号 + 物料汇总数量相等</div></div>
          <div class="kpi" :class="{ bad: chk.summary.DIFF }"><div class="l">数量不一致</div><div class="v">{{ chk.summary.DIFF }}</div><div class="s">旧表与 ERP 单据数量有出入</div></div>
          <div class="kpi"><div class="l">ERP 镜像无此单</div><div class="v">{{ chk.summary.NO_DOC }}</div><div class="s">金蝶单据拉取后再核对</div></div>
        </div>
        <div class="card" style="padding: 0">
          <el-table :data="chk?.rows || []" size="small">
            <el-table-column label="单号" width="190"><template #default="{ row }"><span class="mono">{{ row.doc_no }}</span></template></el-table-column>
            <el-table-column prop="material_code" label="物料编码" />
            <el-table-column prop="history_qty" label="旧表合计" width="110" align="right" />
            <el-table-column label="ERP" width="110" align="right"><template #default="{ row }">{{ row.erp_qty ?? '—' }}</template></el-table-column>
            <el-table-column label="差额" width="100" align="right"><template #default="{ row }"><b :style="{ color: row.state === 'DIFF' ? '#c0392b' : '' }">{{ row.diff ?? '—' }}</b></template></el-table-column>
            <el-table-column label="结果" width="120"><template #default="{ row }"><el-tag size="small" :type="{ OK: 'success', DIFF: 'danger', NO_DOC: 'info' }[row.state]">{{ { OK: '一致', DIFF: '不一致', NO_DOC: '无此单' }[row.state] }}</el-tag></template></el-table-column>
          </el-table>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { hasRole } from '../store'
import { dictName, dictOptions } from '../utils'

const SRC = { SHEET: '原表', ERP: '按出库单回填', VIRTUAL: '未立项' }
const tab = ref('rows')
const rules = ref([])
const columns = ref([])
const virtualProject = ref('试制-未立项')
const rows = ref([])
const total = ref(0)
const batches = ref([])
const q = ref('')
const dest = ref('')
const batch = ref('')
const chk = ref(null)
const last = ref(null)

async function load() {
  if (tab.value === 'check') { chk.value = await api.get(`/api/history/check?batch=${batch.value || ''}`); return }
  const r = await api.get(`/api/history?q=${encodeURIComponent(q.value)}&dest=${dest.value || ''}&batch=${batch.value || ''}`)
  rows.value = r.rows; total.value = r.total; batches.value = r.batches
}
async function upload({ file }) {
  last.value = await api.upload('/api/history/import', file)
  ElMessage.success(`已导入 ${last.value.count} 行`)
  batch.value = last.value.batch
  load()
}
onMounted(async () => {
  const r = await api.get('/api/history/rules')
  rules.value = r.rules; columns.value = r.columns; virtualProject.value = r.virtual_project
  load()
})
</script>

<style scoped>
.filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
</style>
