<template>
  <div>
    <div v-if="!mobile" class="page-head"><h2>待我审核</h2><span class="sub">4 小时未审会提醒，24 小时未审自动升级给您的上级</span></div>
    <div class="card">
      <h3>领料申请（{{ reqs.length }}）</h3>
      <div v-for="r in reqs" :key="r.id" class="row" @click="$router.push(`${p}/req/${r.id}`)">
        <div class="grow">
          <b>{{ r.user }}</b> · {{ r.project }} · {{ dictName('REASON', r.reason_code) }}
          <el-tag v-if="r.urgent" type="danger" size="small">紧急</el-tag>
          <el-tag v-if="r.long_cycle" size="small">长周期 {{ r.long_cycle_months }} 月</el-tag>
          <el-tag v-if="r.escalated_to" type="warning" size="small">升级</el-tag>
          <div class="muted">{{ r.items.map(i => `${i.material}×${i.qty}${i.unit}`).join('，') }} · {{ r.remark }}</div>
          <div class="muted">{{ r.no }} · {{ r.created_at }}</div>
        </div>
        <div class="btns" @click.stop>
          <el-button type="primary" size="small" @click="decide(r, true)">同意</el-button>
          <el-button size="small" @click="$router.push(`${p}/req/${r.id}`)">详情</el-button>
        </div>
      </div>
      <el-empty v-if="!reqs.length" description="没有待审核的申请" :image-size="60" />
    </div>
    <div class="card">
      <h3>续期 / 改归还日申请（{{ lineReqs.length }}）</h3>
      <div v-for="e in lineReqs" :key="e.id" class="row">
        <div class="grow">
          <b>{{ e.operator }}</b> · {{ e.etype === 'RENEW' ? `长周期续期 ${e.extra.months} 个月` : `改归还日 → ${e.extra.new_date}` }}
          <div class="muted"><router-link :to="`${p}/line/${e.line_id}`" class="mono">{{ e.code }}</router-link> · 原归还日 {{ e.due_date }} · {{ e.reason }}</div>
        </div>
        <div class="btns">
          <el-button type="primary" size="small" @click="decideLine(e, true)">同意</el-button>
          <el-button size="small" type="danger" plain @click="decideLine(e, false)">驳回</el-button>
        </div>
      </div>
      <el-empty v-if="!lineReqs.length" description="暂无" :image-size="60" />
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { dictName } from '../utils'

const route = useRoute()
const mobile = computed(() => route.path.startsWith('/m'))
const p = computed(() => mobile.value ? '/m' : '')
const reqs = ref([])
const lineReqs = ref([])
async function load() {
  reqs.value = await api.get('/api/requisitions?scope=approve')
  lineReqs.value = await api.get('/api/line-requests')
}
async function decide(r, approve) {
  await api.post(`/api/requisitions/${r.id}/decide`, { approve })
  ElMessage.success('已同意')
  load()
}
async function decideLine(e, approve) {
  let note = ''
  if (!approve) note = (await ElMessageBox.prompt('驳回原因', '驳回')).value
  await api.post(`/api/line-requests/${e.id}/decide`, { approve, note })
  ElMessage.success('已处理')
  load()
}
onMounted(load)
</script>

<style scoped>
.row { display: flex; gap: 10px; align-items: center; padding: 10px 0; border-bottom: 1px solid var(--line); cursor: pointer; font-size: 13.5px; }
.grow { flex: 1; }
.btns { display: flex; gap: 6px; flex-wrap: wrap; }
</style>
