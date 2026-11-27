<template>
  <div>
    <div v-if="!mobile" class="page-head"><h2>新建领料申请</h2><span class="sub">提交后推送给直属上级审核；审核通过后领料专员才能在金蝶开单</span></div>
    <el-alert v-if="store.user?.frozen" type="error" :closable="false" show-icon style="margin-bottom: 12px"
              title="您因超期未处理已被冻结新领用（含紧急领用），请先处理超期物料并联系直属上级解冻。" />
    <div class="card">
      <el-form label-position="top" :disabled="store.user?.frozen">
        <el-row :gutter="14">
          <el-col :md="12" :xs="24">
            <el-form-item label="项目（只能从 ERP 项目档案选择）" required>
              <RemoteSelect v-model="f.project_id" kind="project" @picked="p => (project = p)" />
              <div v-if="project" class="muted">客户代码：{{ project.customer_code || '—' }}</div>
            </el-form-item>
          </el-col>
          <el-col :md="12" :xs="24">
            <el-form-item label="领料原因" required>
              <el-select v-model="f.reason_code" style="width: 100%">
                <el-option v-for="d in dictOptions('REASON')" :key="d.code" :value="d.code" :label="`${d.code} ${d.name}`" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="明细备注（必填）" required>
          <el-input v-model="f.remark" type="textarea" :rows="2" :placeholder="f.reason_code === 'R01' ? '请写明实际做的验证明细' : '写清用途和明细'" />
        </el-form-item>
        <el-row :gutter="14">
          <el-col :md="8" :xs="24">
            <el-form-item label="预计归还日" required>
              <el-date-picker v-model="f.expected_return" value-format="YYYY-MM-DD" style="width: 100%" :disabled-date="d => d < Date.now() - 86400000" />
            </el-form-item>
          </el-col>
          <el-col :md="8" :xs="12">
            <el-form-item label="长周期物料">
              <el-switch v-model="f.long_cycle" />
              <el-input-number v-if="f.long_cycle" v-model="f.long_cycle_months" :min="1" :max="12" size="small" style="margin-left: 8px; width: 110px" />
              <span v-if="f.long_cycle" class="muted" style="margin-left: 4px">个月</span>
            </el-form-item>
          </el-col>
          <el-col :md="8" :xs="12">
            <el-form-item label="紧急领用（先领后审）"><el-switch v-model="f.urgent" /></el-form-item>
          </el-col>
        </el-row>
        <el-alert v-if="f.long_cycle" type="info" :closable="false" style="margin-bottom: 12px"
                  title="长周期：直属上级审核时一并确认；不触发 2 周冻结，每季度需确认一次“仍在使用”，最长 12 个月，到期需续期。" />
        <el-form-item v-if="f.urgent" label="口头同意情况（谁、何时同意）" required>
          <el-input v-model="f.urgent_note" placeholder="如：组长 11/2 9:00 电话同意" />
          <div class="muted">领料专员可先交接，24 小时内须补齐系统审核和金蝶单，否则进挂账；补审被驳回须全额退库。</div>
        </el-form-item>
        <el-form-item label="物料（扫码或从基础资料选择）" required>
          <div v-for="(it, i) in f.items" :key="i" class="item">
            <RemoteSelect v-model="it.material_id" kind="material" style="flex: 2" @picked="m => (it.m = m)" />
            <el-input-number v-model="it.qty" :min="0" :precision="3" style="width: 150px" />
            <span class="muted" style="width: 30px">{{ it.m?.unit }}</span>
            <el-button v-if="f.items.length > 1" circle :icon="'Close'" @click="f.items.splice(i, 1)" />
          </div>
          <el-button size="small" @click="f.items.push({ material_id: null, qty: 1 })"><el-icon><Plus /></el-icon>添加物料</el-button>
        </el-form-item>
        <el-button type="primary" size="large" :loading="saving" @click="submit">提交申请</el-button>
      </el-form>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { store } from '../store'
import { dictOptions, today } from '../utils'
import RemoteSelect from '../components/RemoteSelect.vue'
import { closeTab } from '../tabs'

const route = useRoute()
const router = useRouter()
const mobile = computed(() => route.path.startsWith('/m'))
const project = ref(null)
const saving = ref(false)
const f = reactive({ project_id: null, reason_code: 'R01', remark: '', expected_return: today(30), long_cycle: false,
  long_cycle_months: 6, urgent: false, urgent_note: '', items: [{ material_id: null, qty: 1 }] })

async function submit() {
  saving.value = true
  try {
    const body = { ...f, items: f.items.filter(i => i.material_id).map(i => ({ material_id: i.material_id, qty: i.qty })) }
    if (!body.long_cycle) body.long_cycle_months = null
    const r = await api.post('/api/requisitions', body)
    ElMessage.success(`已提交 ${r.no}，审核人：${r.approver}`)
    closeTab('/apply', '') // 电脑版：提交后关掉「新建申请」页签，下次打开是空白表单
    router.push(`${mobile.value ? '/m' : ''}/req/${r.id}`)
  } finally { saving.value = false }
}
</script>

<style scoped>
.item { display: flex; gap: 8px; align-items: center; width: 100%; margin-bottom: 8px; }
</style>
