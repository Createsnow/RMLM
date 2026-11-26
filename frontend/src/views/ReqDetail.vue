<template>
  <div v-if="r">
    <div class="page-head">
      <h2>{{ r.no }}</h2>
      <el-tag :type="REQ_STATUS[r.status][1]">{{ REQ_STATUS[r.status][0] }}</el-tag>
      <el-tag v-if="r.urgent" type="danger">紧急</el-tag>
      <el-tag v-if="r.long_cycle">长周期 {{ r.long_cycle_months }} 个月</el-tag>
    </div>
    <div class="card">
      <el-descriptions :column="mobile ? 1 : 3" border size="small">
        <el-descriptions-item label="使用人">{{ r.user }}<span v-if="r.created_by !== r.user" class="muted">（{{ r.created_by }} 代申请）</span></el-descriptions-item>
        <el-descriptions-item label="项目">{{ r.project }} {{ r.project_name }}</el-descriptions-item>
        <el-descriptions-item label="客户代码">{{ r.customer_code || '—' }}</el-descriptions-item>
        <el-descriptions-item label="领料原因">{{ dictName('REASON', r.reason_code) }}</el-descriptions-item>
        <el-descriptions-item label="预计归还日">{{ r.expected_return }}</el-descriptions-item>
        <el-descriptions-item label="提交时间">{{ r.created_at }}</el-descriptions-item>
        <el-descriptions-item label="明细备注" :span="3">{{ r.remark }}</el-descriptions-item>
        <el-descriptions-item v-if="r.urgent" label="口头同意" :span="3">{{ r.urgent_note }}</el-descriptions-item>
        <el-descriptions-item label="审核人">{{ r.approver }}<span v-if="r.escalated_to" class="muted">（已升级至 {{ r.escalated_to }}）</span></el-descriptions-item>
        <el-descriptions-item label="审核结果" :span="2">
          <template v-if="r.decided_by">{{ r.decided_by }}{{ { DELEGATE: '（代理）', ESCALATED: '（升级审核）' }[r.decided_as] || '' }} · {{ r.decided_at }} {{ r.decision_note || '' }}</template>
          <span v-else class="muted">待审核</span>
        </el-descriptions-item>
      </el-descriptions>
    </div>
    <div class="card">
      <h3>物料明细</h3>
      <el-table :data="r.items" size="small">
        <el-table-column label="物料"><template #default="{ row }">{{ row.material_code }} {{ row.material }}<div class="muted">{{ row.spec }}</div></template></el-table-column>
        <el-table-column label="数量" width="110"><template #default="{ row }">{{ row.qty }} {{ row.unit }}</template></el-table-column>
        <el-table-column label="领用行" min-width="160"><template #default="{ row }">
          <router-link v-for="l in row.lines" :key="l.id" :to="`${mobile ? '/m' : ''}/line/${l.id}`" class="mono" style="margin-right: 8px">{{ l.code }}{{ l.erp_bound ? '' : '（待补 ERP 单）' }}</router-link>
          <span v-if="!row.lines.length" class="muted">未开单</span>
        </template></el-table-column>
      </el-table>
    </div>
    <div v-if="r.can_approve" class="card">
      <h3>审核</h3>
      <div class="muted" style="margin-bottom: 8px">只能同意或驳回；要改数量请驳回后由申请人重提。<template v-if="r.long_cycle">同意即确认为长周期物料。</template></div>
      <el-input v-model="note" placeholder="意见（驳回必填）" style="margin-bottom: 10px" />
      <el-button type="primary" @click="decide(true)">同意</el-button>
      <el-button type="danger" plain @click="decide(false)">驳回</el-button>
    </div>
    <el-button v-if="r.can_cancel" @click="cancel">{{ r.status === 'APPROVED' ? '撤回未开单部分' : '撤回申请' }}</el-button>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { REQ_STATUS, dictName } from '../utils'

const route = useRoute()
const mobile = computed(() => route.path.startsWith('/m'))
const r = ref(null)
const note = ref('')
async function load() { r.value = await api.get(`/api/requisitions/${route.params.id}`) }
async function decide(approve) {
  await api.post(`/api/requisitions/${r.value.id}/decide`, { approve, note: note.value })
  ElMessage.success(approve ? '已同意' : '已驳回')
  load()
}
async function cancel() {
  await ElMessageBox.confirm('确定撤回该申请？')
  await api.post(`/api/requisitions/${r.value.id}/cancel`)
  load()
}
onMounted(load)
</script>
