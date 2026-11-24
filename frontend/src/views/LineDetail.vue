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
      <div style="margin: 10px 0 4px"><BalanceBar :line="l" legend /></div>
      <div class="muted num">领用 {{ l.qty }} · 耗用 {{ l.consumed }} · 损耗 {{ l.loss }} · 拆出 {{ l.split_out }} · 退库 {{ l.returned }} · 报废 {{ l.scrapped }}</div>
      <el-alert v-if="l.locked" type="error" :closable="false" title="该行已锁定（结存异常），请联系 IT 处理" style="margin-top: 10px" />
      <el-alert v-if="l.pending_transfer" :type="l.pending_transfer.external && !l.pending_transfer.to_user ? 'warning' : 'info'" :closable="false" style="margin-top: 10px"
                :title="l.pending_transfer.to_user ? `已发起转交给 ${l.pending_transfer.to_user}${l.pending_transfer.external ? `（外部：${l.pending_transfer.external}）` : ''}，等待对方确认`
                  : `外部·待认领：${l.pending_transfer.external}。持有人不变，等待领料专员指派到系统账号`">
        <div class="tr-ops">
          <template v-if="l.actions.assign_transfer">
            <RemoteSelect v-model="assignTo" kind="user" :exclude="[l.holder_id]" style="width: 200px" />
            <el-button size="small" type="primary" :disabled="!assignTo" @click="assignTransfer">指派受让人</el-button>
          </template>
          <el-button v-if="l.actions.cancel_transfer" size="small" @click="cancelTransfer">撤回转交</el-button>
        </div>
      </el-alert>
      <el-alert v-if="l.lab_pending.length" type="warning" :closable="false" style="margin-top: 10px"
                :title="`有 ${l.lab_pending.length} 次可靠性实验（D02）还没回填结论，送实验室 30 天内须回填`" />
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
        <el-button v-if="l.actions.bind_code" type="warning" plain @click="op('bind_code')">补绑仓储码</el-button>
      </div>
    </div>

    <div class="card">
      <el-descriptions :column="mobile ? 1 : 3" border size="small">
        <el-descriptions-item label="物料码"><span class="mono">{{ l.code }}</span>
          <span class="muted"> {{ { WMS: '仓储码', LINE: '行号（未绑仓储码）', RM: '旧 RM 码，待补绑仓储码' }[l.code_kind] }}</span></el-descriptions-item>
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
            <span v-if="e.extra?.external && !e.to_user"> → 外部：{{ e.extra.external }}</span>
            <span v-if="e.extra?.ship_no"> · 出货单 <span class="mono">{{ e.extra.ship_no }}</span></span>
            <span v-if="e.etype === 'CONCLUSION'"> · 对应 #{{ e.extra?.for }}</span>
            <el-tag v-if="e.status !== 'EFFECTIVE'" size="small" :type="e.status === 'PENDING' ? 'warning' : 'info'" style="margin-left: 6px">{{ { PENDING: e.etype === 'TRANSFER_REQ' && !e.to_user ? '待认领' : '待确认', ASSIGNED: '已指派', CANCELLED: '已撤回' }[e.status] || '已拒绝' }}</el-tag>
            <el-tag v-if="e.reversed" size="small" type="info" style="margin-left: 6px">已冲销</el-tag>
            <el-tag v-if="e.is_proxy" size="small" type="warning" style="margin-left: 6px">代登</el-tag>
            <div class="muted">经手 {{ e.operator }}<span v-if="e.reason"> · {{ e.reason }}</span><span v-if="e.note"> · {{ e.note }}</span>
              <span v-if="e.extra?.children"> · 新码 {{ e.extra.children.join('、') }}</span>
              <span v-if="e.extra?.no_code"> · 未扫码（{{ e.extra.no_code }}）</span>
              <span v-if="e.etype === 'BIND_CODE'"> · {{ e.extra.old }} → {{ e.extra.new }}</span></div>
            <PhotoThumbs v-if="e.photos.length" :ids="e.photos" />
            <el-button v-if="l.lab_pending.includes(e.id) && l.actions.conclusion" size="small" type="warning" plain @click="openConclusion(e)">回填实验结论</el-button>
            <el-button v-if="l.actions.reversal && canReverse(e)" size="small" text type="danger" @click="reverse(e)">发起冲销</el-button>
          </div>
        </el-timeline-item>
      </el-timeline>
    </div>

    <div class="card" v-if="l.codes.length">
      <h3>物料码与拆分关系</h3>
      <el-table :data="l.codes" size="small">
        <el-table-column label="物料码"><template #default="{ row }"><span class="mono">{{ row.code }}</span></template></el-table-column>
        <el-table-column prop="layer" label="层" width="50" />
        <el-table-column label="上级码"><template #default="{ row }"><span class="mono">{{ row.parent_code || '—' }}</span></template></el-table-column>
        <el-table-column label="来源" width="90"><template #default="{ row }">{{ row.source === 'WMS' ? '仓储' : '旧 RM 码' }}</template></el-table-column>
        <el-table-column label="码状态" width="130"><template #default="{ row }">{{ row.state === 'RETIRED' ? '已改用仓储码' : dictName('CODE_STATUS', row.state) }}</template></el-table-column>
      </el-table>
      <div v-if="l.children.length" style="margin-top: 10px">子行：
        <router-link v-for="c in l.children" :key="c.id" :to="`${p}/line/${c.id}`" class="mono" style="margin-right: 10px">{{ c.code }}（{{ c.qty }}）{{ c.voided ? '已作废' : '' }}</router-link>
      </div>
      <div v-if="l.parent_id" style="margin-top: 10px">父行：<router-link :to="`${p}/line/${l.parent_id}`">查看</router-link></div>
    </div>
    <LineOps ref="ops" :line="l" :mobile="mobile" @done="onDone" />
    <el-dialog v-model="cdlg" title="回填实验结论" :width="mobile ? '94%' : '480px'" append-to-body>
      <div class="muted" style="margin-bottom: 8px">{{ cEvent?.created_at }} 送可靠性实验 {{ cEvent?.qty }} {{ l.unit }}</div>
      <el-input v-model="cf.text" type="textarea" :rows="3" placeholder="实验项目、条件与结论，如：高低温 500 次循环后插损合格" />
      <div style="margin-top: 10px"><PhotoPicker v-model="cf.photos" /></div>
      <template #footer><el-button @click="cdlg = false">取消</el-button><el-button type="primary" @click="submitConclusion">提交</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { STATUS_TYPE, dictName, fmtMoney } from '../utils'
import LineOps from '../components/LineOps.vue'
import PhotoThumbs from '../components/PhotoThumbs.vue'
import PhotoPicker from '../components/PhotoPicker.vue'
import RemoteSelect from '../components/RemoteSelect.vue'
import BalanceBar from '../components/BalanceBar.vue'

const route = useRoute()
const mobile = computed(() => route.path.startsWith('/m'))
const p = computed(() => mobile.value ? '/m' : '')
const l = ref(null)
const ops = ref()
const assignTo = ref(null)
const cdlg = ref(false)
const cEvent = ref(null)
const cf = reactive({ text: '', photos: [] })

async function load() { l.value = await api.get(`/api/lines/${route.params.id}`) }
function op(k) { ops.value.open(k) }
function onDone(res, kind) {
  l.value = res
  if (kind === 'split' && res.result) {
    ElMessageBox.alert(`拆出 ${res.result.children.join('、')}${res.result.remainder_code ? '，余量 ' + res.result.remainder_code : ''}。请确认新标签已贴在对应实物上，原标签撕下作废。`, '拆分完成')
  }
}
async function decideTransfer(accept) {
  l.value = await api.post(`/api/transfers/${l.value.pending_transfer.id}/decide?accept=${accept}`)
  ElMessage.success(accept ? '已接收，您现在是持有人' : '已拒绝')
}
async function assignTransfer() {
  l.value = await api.post(`/api/transfers/${l.value.pending_transfer.id}/assign?to_user_id=${assignTo.value}`)
  assignTo.value = null
  ElMessage.success('已指派，等待受让人确认')
}
async function cancelTransfer() {
  l.value = await api.post(`/api/transfers/${l.value.pending_transfer.id}/cancel`)
  ElMessage.success('已撤回转交')
}
function openConclusion(e) { cEvent.value = e; cf.text = ''; cf.photos = []; cdlg.value = true }
async function submitConclusion() {
  l.value = await api.post(`/api/events/${cEvent.value.id}/conclusion`, { text: cf.text, photos: cf.photos })
  cdlg.value = false
  ElMessage.success('已回填实验结论')
}
const evType = e => ({ RETURN: 'success', SCRAP: 'danger', TRANSFER_OK: 'primary', HOLD: 'info', MOVE: 'info', CONCLUSION: 'success' })[e.etype] || (e.is_loss ? 'warning' : '')
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
.tr-ops { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 6px; }
</style>
