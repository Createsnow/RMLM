<template>
  <el-dialog v-model="visible" :title="titles[kind]" :width="mobile ? '94%' : '520px'" destroy-on-close append-to-body>
    <div class="head">
      <b>{{ line.material }}</b> <span class="mono">{{ line.code }}</span><br />
      <span class="muted">结存 <b class="num">{{ line.balance }}</b> {{ line.unit }} · 持有人 {{ line.holder }}</span>
      <el-tag v-if="isProxy" type="warning" size="small" style="margin-left: 6px">代登，将通知持有人</el-tag>
    </div>
    <el-form label-position="top" @submit.prevent>
      <template v-if="kind === 'sign' && needCodes">
        <el-form-item label="扫描实物上的仓储条码（一个码一条领用行，数量合计须等于本行数量）" required>
          <ScanInput placeholder="扫描或输入仓储条码" @scan="addCode" />
        </el-form-item>
        <div v-for="(c, i) in f.codes" :key="c.barcode" class="code-row">
          <span class="mono">{{ c.barcode }}</span>
          <span class="num">{{ c.qty }} {{ c.unit || line.unit }}</span>
          <el-button size="small" text type="danger" @click="f.codes.splice(i, 1)">移除</el-button>
        </div>
        <el-alert :type="codeSum === +line.qty ? 'success' : 'warning'" :closable="false" show-icon style="margin-bottom: 10px"
                  :title="`已扫 ${f.codes.length} 个码，合计 ${codeSum} / 本行 ${line.qty} ${line.unit}`" />
      </template>

      <template v-if="kind === 'consume'">
        <el-form-item label="本次登记">
          <el-radio-group v-model="f.is_loss">
            <el-radio-button :value="false">耗用</el-radio-button>
            <el-radio-button :value="true">损耗</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="数量" required><el-input-number v-model="f.qty" :min="0" :max="+line.balance" :precision="3" :step="1" style="width: 100%" /></el-form-item>
        <el-form-item v-if="!f.is_loss" label="去向" required>
          <el-radio-group v-model="f.dest">
            <el-radio-button v-for="d in dictOptions('DEST', d => ['D01','D02','D03'].includes(d.code))" :key="d.code" :value="d.code">{{ d.name }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-else label="损耗原因" required>
          <el-select v-model="f.loss_reason" style="width: 100%">
            <el-option v-for="d in dictOptions('LOSS_REASON')" :key="d.code" :value="d.code" :label="`${d.code} ${d.name}`" />
          </el-select>
        </el-form-item>
      </template>

      <template v-if="kind === 'split'">
        <el-form-item label="拆出份数（每份生成新码，需打印标签）" required>
          <div v-for="(p, i) in f.pieces" :key="i" class="piece">
            <el-input-number v-model="f.pieces[i]" :min="0" :precision="3" style="flex: 1" />
            <el-button v-if="f.pieces.length > 1" :icon="'Close'" circle @click="f.pieces.splice(i, 1)" />
          </div>
          <el-button size="small" @click="f.pieces.push(0)"><el-icon><Plus /></el-icon>再拆一份</el-button>
        </el-form-item>
        <el-form-item label="损耗（可为 0）">
          <el-input-number v-model="f.loss_qty" :min="0" :precision="3" style="width: 100%" />
        </el-form-item>
        <el-form-item v-if="f.loss_qty > 0" label="损耗原因" required>
          <el-select v-model="f.loss_reason" style="width: 100%">
            <el-option v-for="d in dictOptions('LOSS_REASON')" :key="d.code" :value="d.code" :label="`${d.code} ${d.name}`" />
          </el-select>
        </el-form-item>
        <el-alert :type="remainder < 0 ? 'error' : 'info'" :closable="false" show-icon
                  :title="`守恒：拆出 ${sumPieces} + 损耗 ${f.loss_qty || 0} + 余量 ${remainder} = 结存 ${line.balance}`" />
        <div class="muted" style="margin-top: 6px">原码提交后立即失效，余量也会换新码；物料码最多三层。</div>
      </template>

      <template v-if="kind === 'hold' || kind === 'move'">
        <el-form-item label="库位 / 工位" required>
          <el-select v-model="f.location_id" filterable style="width: 100%">
            <el-option v-for="l in locations" :key="l.id" :value="l.id" :label="`${l.code} ${l.name}`" />
          </el-select>
        </el-form-item>
        <div class="muted">暂存只改位置，不扣结存。</div>
      </template>

      <template v-if="kind === 'transfer'">
        <el-form-item label="受让人" required><RemoteSelect v-model="f.to_user_id" kind="user" :exclude="[line.holder_id]" /></el-form-item>
        <el-form-item label="转交原因" required><el-input v-model="f.reason" /></el-form-item>
        <div class="muted">受让人确认前不生效，原持有人仍担责；确认后超期责任随新持有人。</div>
      </template>

      <template v-if="kind === 'return'">
        <el-form-item label="实退数量" required><el-input-number v-model="f.qty" :min="0" :max="+line.balance" :precision="3" style="width: 100%" /></el-form-item>
        <div class="muted">还能用的料必须退库。登记后由领料专员合并开金蝶退料单并分摊。</div>
      </template>

      <template v-if="kind === 'scrap'">
        <el-form-item label="报废方式" required>
          <el-radio-group v-model="f.mode">
            <el-radio-button value="RESIDUE">残料（用完后剩余）</el-radio-button>
            <el-radio-button value="WHOLE">整件报废</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <template v-if="f.mode === 'RESIDUE'">
          <el-alert type="info" :closable="false" :title="`将按损耗（L2 残料无法回收）清零 ${line.balance} ${line.unit}，不隔离、不开 ERP 报废单`" />
        </template>
        <template v-else>
          <el-form-item label="数量" required><el-input-number v-model="f.qty" :min="0" :max="+line.balance" :precision="3" style="width: 100%" /></el-form-item>
          <el-form-item label="报废原因" required>
            <el-select v-model="f.reason" style="width: 100%">
              <el-option v-for="d in dictOptions('SCRAP_REASON')" :key="d.code" :value="d.code" :label="d.name" />
            </el-select>
          </el-form-item>
          <el-form-item label="报废暂存箱号" required>
            <el-select v-model="f.box" style="width: 100%">
              <el-option v-for="b in boxes" :key="b.id" :value="b.code" :label="`${b.code} ${b.name}`" />
            </el-select>
          </el-form-item>
          <div class="muted">贴标入箱，每周按批次统一开 ERP 报废单；未挂单前不能结案。</div>
        </template>
      </template>

      <template v-if="kind === 'renew'">
        <el-form-item label="续期月数（1–12）" required><el-input-number v-model="f.months" :min="1" :max="12" /></el-form-item>
        <el-form-item label="原因" required><el-input v-model="f.reason" /></el-form-item>
        <div class="muted">由持有人的直属上级审核。</div>
      </template>

      <template v-if="kind === 'due_change'">
        <el-form-item label="新的预计归还日" required><el-date-picker v-model="f.new_date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item>
        <el-form-item label="原因" required><el-input v-model="f.reason" /></el-form-item>
        <div class="muted">由持有人的直属上级审核。</div>
      </template>

      <el-form-item v-if="['consume','split','hold','move','return','scrap','qconfirm','sign'].includes(kind)" label="说明">
        <el-input v-model="f.note" :placeholder="f.loss_reason === 'L5' ? '损耗原因为「其他」时必须写说明' : '选填'" />
      </el-form-item>
      <el-form-item v-if="needPhoto || ['move','return','qconfirm'].includes(kind)" :label="needPhoto ? '现场照片（必拍）' : '现场照片（选填）'" :required="needPhoto">
        <PhotoPicker v-model="f.photos" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="submit">确认提交</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { store } from '../store'
import { dictOptions } from '../utils'
import PhotoPicker from './PhotoPicker.vue'
import RemoteSelect from './RemoteSelect.vue'
import ScanInput from './ScanInput.vue'

const props = defineProps({ line: { type: Object, required: true }, mobile: Boolean })
const emit = defineEmits(['done'])
const visible = ref(false)
const kind = ref('')
const saving = ref(false)
const locations = ref([])
const boxes = ref([])
const f = reactive({})
const titles = { sign: '签收', consume: '使用登记', split: '拆分换码', hold: 'WIP 暂存', move: '更新位置', transfer: '转交他人',
  return: '退库', scrap: '报废', qconfirm: '季度在用确认', renew: '长周期续期', due_change: '申请改归还日' }
const needPhoto = computed(() => ['sign', 'consume', 'split', 'hold', 'scrap'].includes(kind.value))
const isProxy = computed(() => store.user && props.line.holder_id !== store.user.id && kind.value !== 'sign')
const sumPieces = computed(() => +(f.pieces || []).reduce((a, b) => a + (+b || 0), 0).toFixed(3))
const remainder = computed(() => +(+props.line.balance - sumPieces.value - (+f.loss_qty || 0)).toFixed(3))
const needCodes = computed(() => !!props.line.actions?.sign_needs_codes)
const codeSum = computed(() => +(f.codes || []).reduce((a, c) => a + (+c.qty || 0), 0).toFixed(3))

async function addCode(code) {
  if (f.codes.some(c => c.barcode === code)) return ElMessage.warning(`条码 ${code} 已扫过`)
  const bc = await api.get(`/api/wms/barcodes/${encodeURIComponent(code)}`)
  if (bc.used) return ElMessage.error(`条码 ${code} 已被其他领用行使用`)
  if (bc.material_code !== props.line.material_code) return ElMessage.error(`条码 ${code} 是物料 ${bc.material_code}，与本行物料不符`)
  if (bc.status !== 'OUTBOUND') return ElMessage.error(`条码 ${code} 在仓储系统中为「${bc.status_name}」，不能签收`)
  f.codes.push(bc)
}

async function open(k, preset = {}) {
  kind.value = k
  Object.keys(f).forEach(key => delete f[key])
  Object.assign(f, { qty: +props.line.balance, dest: 'D01', is_loss: false, loss_reason: null, note: '', photos: [],
    pieces: [0], loss_qty: 0, location_id: null, to_user_id: null, reason: '', mode: 'RESIDUE', box: null, months: 6, new_date: null,
    codes: [] })
  if (k === 'sign' && preset.code && needCodes.value) addCode(preset.code).catch(() => {})
  if (k === 'consume') f.qty = 0
  visible.value = true
  if (k === 'hold' || k === 'move') locations.value = (await api.get('/api/locations')).filter(l => l.kind !== 'SCRAP_BOX')
  if (k === 'scrap') boxes.value = await api.get('/api/locations?kind=SCRAP_BOX')
}

async function submit() {
  const id = props.line.id
  const body = {
    sign: () => ['sign', { photos: f.photos, note: f.note, codes: f.codes.map(c => c.barcode) }],
    consume: () => ['consume', { qty: f.qty, dest: f.is_loss ? null : f.dest, is_loss: f.is_loss, loss_reason: f.loss_reason, note: f.note, photos: f.photos }],
    split: () => ['split', { pieces: f.pieces.filter(p => +p > 0), loss_qty: f.loss_qty, loss_reason: f.loss_reason, note: f.note, photos: f.photos }],
    hold: () => ['hold', { location_id: f.location_id, note: f.note, photos: f.photos }],
    move: () => ['move', { location_id: f.location_id, note: f.note, photos: f.photos }],
    transfer: () => ['transfer', { to_user_id: f.to_user_id, reason: f.reason }],
    return: () => ['return', { qty: f.qty, note: f.note, photos: f.photos }],
    scrap: () => ['scrap', { mode: f.mode, qty: f.qty, reason: f.reason, box: f.box, note: f.note, photos: f.photos }],
    qconfirm: () => ['qconfirm', { note: f.note, photos: f.photos }],
    renew: () => ['renew', { months: f.months, reason: f.reason }],
    due_change: () => ['due-change', { new_date: f.new_date, reason: f.reason }]
  }[kind.value]()
  saving.value = true
  try {
    const res = await api.post(`/api/lines/${id}/${body[0]}`, body[1])
    ElMessage.success(kind.value === 'transfer' ? '已发起转交，等待对方确认' : (kind.value === 'renew' || kind.value === 'due_change') ? '已提交，等待直属上级审核' : '已登记')
    visible.value = false
    emit('done', res, kind.value)
  } finally { saving.value = false }
}

defineExpose({ open })
</script>

<style scoped>
.head { background: #f7f9fc; border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; margin-bottom: 12px; font-size: 13px; }
.piece { display: flex; gap: 6px; width: 100%; margin-bottom: 6px; }
.code-row { display: flex; gap: 12px; align-items: center; padding: 4px 0; border-bottom: 1px dashed var(--line); font-size: 13px; }
.code-row .num { margin-left: auto; }
</style>
