<template>
  <div>
    <div class="page-head">
      <h2 v-if="!embedded">报废批次</h2><span class="sub">整件报废先贴标入箱，每周按箱号生成批次清单，统一开金蝶报废单核销；残料不走此流程</span>
      <span class="grow" />
      <el-button v-if="hasRole('KEEPER')" type="primary" @click="build">生成本周批次</el-button>
    </div>
    <div class="card">
      <h3>待入批次的整件报废（{{ pending.length }}）</h3>
      <el-table :data="pending" size="small">
        <el-table-column label="物料码" width="130"><template #default="{ row }"><router-link :to="`/line/${row.line_id}`" class="mono">{{ row.code }}</router-link></template></el-table-column>
        <el-table-column label="物料"><template #default="{ row }">{{ row.material_code }} {{ row.material }}</template></el-table-column>
        <el-table-column label="数量" width="100"><template #default="{ row }">{{ row.rest }} {{ row.unit }}</template></el-table-column>
        <el-table-column prop="box" label="箱号" width="90" />
        <el-table-column prop="holder" label="判废人" width="90" />
        <el-table-column prop="created_at" label="时间" width="160" />
      </el-table>
    </div>
    <div class="card">
      <h3>批次</h3>
      <el-table :data="batches" size="small">
        <el-table-column prop="no" label="批次号" width="170" />
        <el-table-column prop="box" label="箱号" width="80" />
        <el-table-column prop="week" label="周" width="90" />
        <el-table-column label="明细"><template #default="{ row }">{{ row.items.map(i => `${i.code} ${i.material}×${i.qty}`).join('；') }}</template></el-table-column>
        <el-table-column label="状态" width="220"><template #default="{ row }">
          <template v-if="row.status === 'BOUND'"><el-tag type="success" size="small">已核销 {{ row.erp_doc_no }}</el-tag>
            <el-tag v-if="row.received" size="small" style="margin-left: 4px">实物已交仓</el-tag></template>
          <template v-else-if="hasRole('KEEPER')">
            <el-select v-model="pick[row.id]" placeholder="选报废出库单" size="small" style="width: 140px">
              <el-option v-for="d in docs" :key="d.id" :value="d.id" :label="d.no" />
            </el-select>
            <el-button size="small" type="primary" :disabled="!pick[row.id]" @click="bind(row)">核销</el-button>
          </template>
          <el-tag v-else type="warning" size="small">待开单</el-tag>
        </template></el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
defineProps({ embedded: Boolean })
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { hasRole } from '../store'

const pending = ref([])
const batches = ref([])
const docs = ref([])
const pick = reactive({})
async function load() {
  pending.value = await api.get('/api/scrap/pending')
  batches.value = await api.get('/api/scrap/batches')
  if (hasRole('KEEPER')) docs.value = await api.get('/api/erp/docs?doc_type=BF')
}
async function build() { const r = await api.post('/api/scrap/batches/build'); ElMessage.success(r.created.length ? `已生成 ${r.created.join('、')}` : '没有新的待处置报废'); load() }
async function bind(row) {
  const r = await api.post(`/api/scrap/batches/${row.id}/bind?erp_doc_id=${pick[row.id]}`)
  ElMessage[r.leftover.length ? 'warning' : 'success'](r.leftover.length ? '已核销，有未摊完数量进入差异池' : '已核销')
  load()
}
onMounted(load)
</script>
