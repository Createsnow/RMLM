<template>
  <div>
    <div class="page-head"><h2 v-if="!embedded">退库分摊</h2><span class="sub">多对多：一张退料单可摊到多行领料，一行领料也可分多次退；同一行先领先退</span></div>
    <el-row :gutter="14">
      <el-col :md="15" :xs="24">
        <div class="card" style="padding: 0">
          <el-table :data="rows" @selection-change="v => (sel = v)">
            <el-table-column v-if="hasRole('KEEPER')" type="selection" width="40" />
            <el-table-column label="物料码" width="130"><template #default="{ row }"><router-link :to="`/line/${row.line_id}`" class="mono">{{ row.code }}</router-link></template></el-table-column>
            <el-table-column label="物料"><template #default="{ row }">{{ row.material_code }} {{ row.material }}</template></el-table-column>
            <el-table-column label="退库 / 待摊" width="120"><template #default="{ row }">{{ row.qty }} / <b>{{ row.rest }}</b> {{ row.unit }}</template></el-table-column>
            <el-table-column prop="holder" label="持有人" width="80" />
            <el-table-column prop="source_doc" label="来源领料单" width="160" />
            <el-table-column prop="created_at" label="登记时间" width="155" />
          </el-table>
        </div>
      </el-col>
      <el-col :md="9" :xs="24" v-if="hasRole('KEEPER')">
        <div class="card">
          <h3>匹配金蝶退料单</h3>
          <div class="muted" style="margin-bottom: 8px">先在金蝶合并开退料单，录入 / 导入到「ERP 单据」后在此选择。</div>
          <el-select v-model="docId" filterable placeholder="选择退料入库单" style="width: 100%">
            <el-option v-for="d in docs" :key="d.id" :value="d.id" :label="`${d.no}（${d.lines.map(l => l.material + '×' + l.qty).join('，')}）`" />
          </el-select>
          <div style="margin: 10px 0">已选 <b>{{ sel.length }}</b> 条退库登记，合计待摊 <b>{{ selSum }}</b></div>
          <el-button type="primary" :disabled="!docId || !sel.length" style="width: 100%" @click="allocate">按物料分摊</el-button>
          <el-alert v-if="result" :type="result.leftover.length ? 'warning' : 'success'" :closable="false" style="margin-top: 10px"
                    :title="`已生成 ${result.allocations} 条分摊${result.cross_doc ? '（跨单匹配）' : ''}${result.leftover.length ? '；未摊完：' + result.leftover.map(x => '第' + x.line_no + '行 ' + x.unallocated).join('，') + '，已进差异池' : ''}`" />
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
defineProps({ embedded: Boolean })
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { hasRole } from '../store'

const rows = ref([])
const sel = ref([])
const docs = ref([])
const docId = ref(null)
const result = ref(null)
const selSum = computed(() => +sel.value.reduce((a, r) => a + +r.rest, 0).toFixed(3))
async function load() {
  rows.value = await api.get('/api/returns/pending')
  if (hasRole('KEEPER')) docs.value = await api.get('/api/erp/docs?doc_type=TL')
}
async function allocate() {
  result.value = await api.post('/api/returns/allocate', { erp_doc_id: docId.value, event_ids: sel.value.map(r => r.event_id) })
  load()
}
onMounted(load)
</script>
