<template>
  <div>
    <div class="page-head"><h2>开单绑定</h2><span class="sub">在金蝶开领料单时请在备注里写中台申请号，系统自动匹配；匹配不上再手工绑定（一物一行）</span></div>
    <div class="card" v-if="sug.length">
      <h3>自动匹配建议（{{ sug.length }}）</h3>
      <el-table :data="sug" size="small">
        <el-table-column prop="req_no" label="申请号" />
        <el-table-column label="ERP 单据"><template #default="{ row }">{{ row.erp_doc_no }} 第 {{ row.erp_line_no }} 行</template></el-table-column>
        <el-table-column prop="qty" label="ERP 数量" width="100" />
        <el-table-column width="100"><template #default="{ row }"><el-button size="small" type="primary" @click="bind(row.req_item_id, row.erp_line_id)">绑定</el-button></template></el-table-column>
      </el-table>
    </div>
    <div class="card">
      <h3>待开单的申请（已审核，或紧急领用）</h3>
      <el-table :data="items" size="small">
        <el-table-column label="申请" width="200"><template #default="{ row }">
          <router-link :to="`/req/${row.req.id}`">{{ row.req.no }}</router-link>
          <el-tag v-if="row.req.urgent" type="danger" size="small" style="margin-left: 4px">紧急</el-tag>
          <el-tag v-if="row.req.status === 'PENDING'" type="warning" size="small" style="margin-left: 4px">待审</el-tag>
          <div class="muted">{{ row.req.user }} · {{ row.req.project }}</div>
        </template></el-table-column>
        <el-table-column label="物料"><template #default="{ row }">{{ row.it.material_code }} {{ row.it.material }}</template></el-table-column>
        <el-table-column label="申请数量" width="100"><template #default="{ row }">{{ row.it.qty }} {{ row.it.unit }}</template></el-table-column>
        <el-table-column label="已有领用行" width="160"><template #default="{ row }">
          <span v-for="l in row.it.lines" :key="l.id" class="mono">{{ l.code }}{{ l.erp_bound ? '' : '（待补单）' }} </span>
        </template></el-table-column>
        <el-table-column label="操作" width="260"><template #default="{ row }">
          <el-button v-if="needsBind(row)" size="small" type="primary" @click="openBind(row)">绑定 ERP 单行</el-button>
          <el-button v-if="row.req.urgent && !row.it.lines.length" size="small" type="danger" plain @click="handover(row)">紧急先交接</el-button>
        </template></el-table-column>
      </el-table>
    </div>
    <el-dialog v-model="dlg" title="绑定金蝶领料单行" width="560px">
      <div class="muted" style="margin-bottom: 8px">{{ cur?.it.material_code }} {{ cur?.it.material }} · 申请 {{ cur?.it.qty }} {{ cur?.it.unit }}</div>
      <el-table :data="candidates" size="small" highlight-current-row @current-change="r => (pick = r)">
        <el-table-column prop="doc_no" label="单据号" />
        <el-table-column prop="line_no" label="行" width="50" />
        <el-table-column prop="qty" label="数量" width="80" />
        <el-table-column label="过账" width="60"><template #default="{ row }">{{ row.posted ? '是' : '否' }}</template></el-table-column>
      </el-table>
      <div class="muted" style="margin-top: 8px">只列出同物料、未绑定的领料出库单 / A 转 B 单行。ERP 数量与申请不一致时以 ERP 为准，差异进对账。</div>
      <template #footer><el-button type="primary" :disabled="!pick" @click="bind(cur.it.id, pick.id); dlg = false">绑定</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'

const reqs = ref([])
const sug = ref([])
const docs = ref([])
const dlg = ref(false)
const cur = ref(null)
const pick = ref(null)
const items = computed(() => reqs.value.flatMap(r => r.items.map(it => ({ req: r, it }))).filter(x => needsBind(x) || (x.req.urgent && !x.it.lines.length)))
const needsBind = x => x.req.status === 'APPROVED' ? !x.it.lines.some(l => l.erp_bound) : x.it.lines.some(l => !l.erp_bound)
const candidates = computed(() => !cur.value ? [] : docs.value.flatMap(d => d.lines.filter(l => !l.bound_line && l.material_code === cur.value.it.material_code)
  .map(l => ({ ...l, doc_no: d.no, posted: d.posted }))))

async function load() {
  reqs.value = await api.get('/api/requisitions?scope=issue')
  sug.value = await api.get('/api/erp/suggestions')
  docs.value = [...await api.get('/api/erp/docs?doc_type=LL'), ...await api.get('/api/erp/docs?doc_type=AB')]
}
function openBind(row) { cur.value = row; pick.value = null; dlg.value = true }
async function bind(req_item_id, erp_line_id) {
  const l = await api.post('/api/erp/bind', { req_item_id, erp_line_id })
  ElMessage.success(`已生成领用行 ${l.code}，已通知使用人签收`)
  load()
}
async function handover(row) {
  await ElMessageBox.confirm(`确认已获直属上级口头同意（${row.req.urgent_note}），先交接给 ${row.req.user}？24 小时内须补审核与金蝶单。`, '紧急先交接')
  const l = await api.post(`/api/requisitions/${row.req.id}/urgent-handover/${row.it.id}`)
  ElMessage.success(`已交接 ${l.code}，请使用人签收`)
  load()
}
onMounted(load)
</script>
