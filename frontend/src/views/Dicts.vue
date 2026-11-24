<template>
  <div>
    <div class="page-head">
      <h2>字典与规则</h2><span class="sub">业务只能选、不能加；变更由系统管理员提出、研发副总批准，只能新增 / 停用 / 改名，不能删除</span>
      <span class="grow" />
      <el-button v-if="hasRole('ADMIN')" type="primary" @click="dlg = true">提出变更</el-button>
    </div>
    <el-row :gutter="14">
      <el-col v-for="(items, t) in store.dicts" :key="t" :md="12" :xs="24">
        <div class="card"><h3>{{ NAMES[t] || t }}</h3>
          <el-table :data="items" size="small">
            <el-table-column prop="code" label="编码" width="70" />
            <el-table-column prop="name" label="名称" width="140" />
            <el-table-column prop="meaning" label="说明" />
            <el-table-column label="状态" width="70"><template #default="{ row }">{{ row.active ? '' : '已停用' }}</template></el-table-column>
          </el-table>
        </div>
      </el-col>
    </el-row>
    <div class="card"><h3>现有台账 → 新系统的关键转换规则 <span class="muted">历史基线导入用同一套规则</span></h3>
      <el-table :data="rules" size="small">
        <el-table-column prop="name" label="规则" width="150" />
        <el-table-column label="现有字段（自由文本）关键词" width="260"><template #default="{ row }">{{ row.keywords.join(' / ') || '—' }}</template></el-table-column>
        <el-table-column label="统一为" width="130"><template #default="{ row }">{{ row.dest ? dictName('DEST', row.dest) : '—' }}</template></el-table-column>
        <el-table-column prop="handling" label="处理方式" />
      </el-table>
    </div>
    <div class="card"><h3>变更记录</h3>
      <el-table :data="changes" size="small">
        <el-table-column prop="dict_type" label="字典" width="130" />
        <el-table-column prop="action" label="动作" width="80" />
        <el-table-column label="编码 / 名称"><template #default="{ row }">{{ row.code }} {{ row.name || '' }}</template></el-table-column>
        <el-table-column prop="reason" label="原因" />
        <el-table-column prop="requested_by" label="提出人" width="90" />
        <el-table-column label="状态" width="170"><template #default="{ row }">
          <template v-if="row.status === 'PENDING' && hasRole('VP')">
            <el-button size="small" type="primary" @click="decide(row, true)">批准</el-button><el-button size="small" @click="decide(row, false)">驳回</el-button>
          </template>
          <span v-else>{{ { PENDING: '待批准', APPROVED: '已批准', REJECTED: '已驳回' }[row.status] }}</span>
        </template></el-table-column>
      </el-table>
    </div>
    <el-dialog v-model="dlg" title="提出字典变更" width="460px">
      <el-form label-width="70px">
        <el-form-item label="字典"><el-select v-model="f.dict_type"><el-option v-for="(n, k) in NAMES" :key="k" :value="k" :label="n" /></el-select></el-form-item>
        <el-form-item label="动作"><el-radio-group v-model="f.action"><el-radio-button value="ADD">新增</el-radio-button><el-radio-button value="DISABLE">停用</el-radio-button><el-radio-button value="RENAME">改名</el-radio-button></el-radio-group></el-form-item>
        <el-form-item label="编码"><el-input v-model="f.code" /></el-form-item>
        <el-form-item v-if="f.action !== 'DISABLE'" label="名称"><el-input v-model="f.name" /></el-form-item>
        <el-form-item v-if="f.action === 'ADD'" label="说明"><el-input v-model="f.meaning" /></el-form-item>
        <el-form-item label="原因"><el-input v-model="f.reason" /></el-form-item>
      </el-form>
      <template #footer><el-button type="primary" @click="submit">提交研发副总批准</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { hasRole, store } from '../store'
import { dictName, loadDicts } from '../utils'

const NAMES = { DEST: '去向 DICT_DEST', LINE_STATUS: '行状态 DICT_STATUS', CODE_STATUS: '物料码状态', REASON: '领料原因', LOSS_REASON: '损耗原因',
  SCRAP_REASON: '报废原因', RECON_REASON: '差异原因码', DOC_TYPE: 'ERP 单据类型' }
const changes = ref([])
const rules = ref([])
const dlg = ref(false)
const f = reactive({ dict_type: 'LOSS_REASON', action: 'ADD', code: '', name: '', meaning: '', reason: '' })
async function load() {
  await loadDicts(true)
  changes.value = await api.get('/api/dict-changes')
  rules.value = (await api.get('/api/history/rules')).rules
}
async function submit() { await api.post('/api/dict-changes', f); ElMessage.success('已提交'); dlg.value = false; load() }
async function decide(row, approve) { await api.post(`/api/dict-changes/${row.id}/decide?approve=${approve}`); load() }
onMounted(load)
</script>
