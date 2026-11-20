<template>
  <div v-if="l">
    <div class="card">
      <div class="top">
        <div>
          <div class="name">{{ l.material }} <span class="muted">{{ l.material_code }}</span></div>
          <div class="muted">{{ l.spec }}</div>
          <div style="margin-top: 6px">
            <el-tag :type="STATUS_TYPE[l.status]">{{ l.status_name }}</el-tag>
            <el-tag v-for="fl in l.flags" :key="fl" type="warning" size="small" class="flag" style="margin-left: 4px">{{ fl }}</el-tag>
          </div>
        </div>
        <div class="bal">
          <div class="muted">当前结存</div>
          <div class="balance-big num">{{ l.balance }} <small>{{ l.unit }}</small></div>
          <div class="muted">¥{{ fmtMoney(l.balance_amount) }}</div>
        </div>
      </div>
      <el-progress :percentage="usedPct" :show-text="false" style="margin: 10px 0 4px" />
      <div class="muted num">领用 {{ l.qty }} · 耗用 {{ l.consumed }} · 损耗 {{ l.loss }} · 拆出 {{ l.split_out }} · 退库 {{ l.returned }} · 报废 {{ l.scrapped }}</div>
      <el-alert v-if="l.locked" type="error" :closable="false" title="该行已锁定（结存异常），请联系 IT 处理" style="margin-top: 10px" />
      <el-alert v-if="l.pending_transfer" type="info" :closable="false" style="margin-top: 10px"
                :title="`已发起转交给 ${l.pending_transfer.to_user}，等待对方确认`" />
      <div class="actions-bar">
        <el-button v-if="l.actions.sign" type="primary" @click="op('sign')">本人签收</el-button>
        <el-button v-if="l.actions.confirm_transfer" type="primary" @click="decideTransfer(true)">确认接收转交</el-button>
        <el-button v-if="l.actions.confirm_transfer" @click="decideTransfer(false)">拒绝转交</el-button>
        <el-button v-if="l.actions.consume" type="primary" @click="op('consume')">使用 / 损耗</el-button>
        <el-button v-if="l.actions.split" @click="op('split')">拆分换码</el-button>
        <el-button v-if="l.actions.hold" @click="op('hold')">WIP 暂存</el-button>
        <el-button v-if="l.actions.move" @click="op('move')">更新位置</el-button>
        <el-button v-if="l.actions.transfer" @click="op('transfer')">转交</el-button>
        <el-button v-if="l.actions.return" @click="op('return')">退库</el-button>
        <el-button v-if="l.actions.scrap" type="danger" plain @click="op('scrap')">报废</el-button>
        <el-button v-if="l.actions.qconfirm" @click="op('qconfirm')">季度在用确认</el-button>
        <el-button v-if="l.actions.renew" @click="op('renew')">续期</el-button>
        <el-button v-if="l.actions.due_change && !l.long_cycle_max" @click="op('due_change')">改归还日</el-button>
        <el-button @click="$router.push(`/labels?ids=${l.id}`)"><el-icon><Printer /></el-icon>标签</el-button>
      </div>
    </div>

    <div class="card">
      <el-descriptions :column="mobile ? 1 : 3" border size="small">
        <el-descriptions-item label="物料码"><span class="mono">{{ l.code }}</span></el-descriptions-item>
        <el-descriptions-item label="项目">{{ l.project }} {{ l.project_name }}</el-descriptions-item>
        <el-descriptions-item label="客户代码">{{ l.customer_code || '—' }}</el-descriptions-item>
        <el-descriptions-item label="领用人">{{ l.user }}</el-descriptions-item>
        <el-descriptions-item label="当前持有人">{{ l.holder }}</el-descriptions-item>
        <el-descriptions-item label="位置">{{ l.location || '—' }}</el-descriptions-item>
        <el-descriptions-item label="预计归还日"><span :style="{ color: l.overdue ? '#c0392b' : '' }">{{ l.due_date }}{{ l.overdue ? '（已超期）' : '' }}</span></el-descriptions-item>
        <el-descriptions-item v-if="l.long_cycle_max" label="长周期最长期限">{{ l.long_cycle_max }}</el-descriptions-item>
        <el-descriptions-item label="ERP 单据">{{ l.erp_doc_no ? `${l.erp_doc_no} 第 ${l.erp_line_no} 行${l.erp_posted ? '' : '（未过账）'}` : '待补' }}</el-descriptions-item>
        <el-descriptions-item label="申请">
          <router-link :to="`${p}/req/${l.requisition.id}`">{{ l.requisition.no }}</router-link> · {{ dictName('REASON', l.requisition.reason_code) }}
        </el-descriptions-item>
        <el-descriptions-item label="单价">{{ l.unit_price ?? '—' }}</el-descriptions-item>
      </el-descriptions>
    </div>

    <div class="card">
      <h3>事件流水（只增不改）</h3>
      <el-timeline>
        <el-timeline-item v-for="e in [...l.events].reverse()" :key="e.id" :timestamp="e.created_at" :type="evType(e)" placement="top">
          <div class="ev">
            <b>{{ e.etype_name }}</b>
            <span v-if="+e.qty" class="num"> {{ e.qty }} {{ l.unit }}</span>
            <span v-if="e.dest"> · {{ dictName('DEST', e.dest) }}</span>
            <span v-if="e.is_loss"> · 损耗 {{ dictName('LOSS_REASON', e.loss_reason) }}</span>
            <span v-if="e.scrap_mode === 'WHOLE'"> · 整件 {{ dictName('SCRAP_REASON', e.scrap_reason) }} · {{ e.box }}</span>
            <span v-if="e.scrap_mode === 'RESIDUE'"> · 残料直接报废</span>
            <span v-if="e.location"> · {{ e.location }}</span>
            <span v-if="e.to_user"> → {{ e.to_user }}</span>
            <el-tag v-if="e.status !== 'EFFECTIVE'" size="small" :type="e.status === 'PENDING' ? 'warning' : 'info'" style="margin-left: 6px">{{ e.status === 'PENDING' ? '待确认' : '已拒绝' }}</el-tag>
            <el-tag v-if="e.reversed" size="small" type="info" style="margin-left: 6px">已冲销</el-tag>
            <el-tag v-if="e.is_proxy" size="small" type="warning" style="margin-left: 6px">代登</el-tag>
            <div class="muted">经手 {{ e.operator }}<span v-if="e.reason"> · {{ e.reason }}</span><span v-if="e.note"> · {{ e.note }}</span>
              <span v-if="e.extra?.children"> · 新码 {{ e.extra.children.join('、') }}</span></div>
            <PhotoThumbs v-if="e.photos.length" :ids="e.photos" />
            <el-button v-if="l.actions.reversal && canReverse(e)" size="small" text type="danger" @click="reverse(e)">发起冲销</el-button>
          </div>
        </el-timeline-item>
      </el-timeline>
    </div>

    <div class="card" v-if="l.codes.length > 1 || l.children.length || l.parent_id">
      <h3>物料码与拆分关系</h3>
      <el-table :data="l.codes" size="small">
        <el-table-column label="物料码"><template #default="{ row }"><span class="mono">{{ row.code }}</span></template></el-table-column>
        <el-table-column prop="layer" label="层" width="50" />
        <el-table-column label="上级码"><template #default="{ row }"><span class="mono">{{ row.parent_code || '—' }}</span></template></el-table-column>
        <el-table-column label="码状态" width="130"><template #default="{ row }">{{ dictName('CODE_STATUS', row.state) }}</template></el-table-column>
      </el-table>
      <div v-if="l.children.length" style="margin-top: 10px">子行：
        <router-link v-for="c in l.children" :key="c.id" :to="`${p}/line/${c.id}`" class="mono" style="margin-right: 10px">{{ c.code }}（{{ c.qty }}）{{ c.voided ? '已作废' : '' }}</router-link>
      </div>
      <div v-if="l.parent_id" style="margin-top: 10px">父行：<router-link :to="`${p}/line/${l.parent_id}`">查看</router-link></div>
    </div>
    <LineOps ref="ops" :line="l" :mobile="mobile" @done="onDone" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { STATUS_TYPE, dictName, fmtMoney } from '../utils'
import LineOps from '../components/LineOps.vue'
import PhotoThumbs from '../components/PhotoThumbs.vue'

const route = useRoute()
const router = useRouter()
const mobile = computed(() => route.path.startsWith('/m'))
const p = computed(() => mobile.value ? '/m' : '')
const l = ref(null)
const ops = ref()
const usedPct = computed(() => l.value && +l.value.qty ? Math.min(100, Math.round((1 - +l.value.balance / +l.value.qty) * 100)) : 0)

async function load() { l.value = await api.get(`/api/lines/${route.params.id}`) }
function op(k) { ops.value.open(k) }
function onDone(res, kind) {
  l.value = res
  if (kind === 'split' && res.result) {
    ElMessageBox.confirm(`已生成新码：${res.result.children.join('、')}${res.result.remainder_code ? '，余量新码 ' + res.result.remainder_code : ''}。请打印并粘贴新标签。`, '拆分完成', { confirmButtonText: '打印标签' })
      .then(() => router.push(`/labels?codes=${[...res.result.children, res.result.remainder_code].filter(Boolean).join(',')}`)).catch(() => {})
  }
}
async function decideTransfer(accept) {
  l.value = await api.post(`/api/transfers/${l.value.pending_transfer.id}/decide?accept=${accept}`)
  ElMessage.success(accept ? '已接收，您现在是持有人' : '已拒绝')
}
const evType = e => ({ RETURN: 'success', SCRAP: 'danger', TRANSFER_OK: 'primary', HOLD: 'info', MOVE: 'info' })[e.etype] || (e.is_loss ? 'warning' : '')
const canReverse = e => ['CONSUME', 'RETURN', 'SCRAP', 'SPLIT_OUT'].includes(e.etype) && e.status === 'EFFECTIVE' && !e.reversed
async function reverse(e) {
  const { value } = await ElMessageBox.prompt('冲销原因（提交后由 IT 复核生效）', `冲销 ${e.etype_name} ${e.qty}`)
  await api.post('/api/reversals', { event_id: e.id, reason: value })
  ElMessage.success('已提交冲销，等待 IT 复核')
  load()
}
onMounted(load)
</script>

<style scoped>
.top { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.name { font-size: 18px; font-weight: 700; }
.bal { text-align: right; }
.ev { font-size: 13.5px; }
</style>
