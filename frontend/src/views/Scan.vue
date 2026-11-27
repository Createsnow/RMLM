<template>
  <div>
    <div v-if="!mobile" class="page-head"><h2>扫码</h2><span class="sub">扫物料码 / ERP 单据号 / 库位码 → 带出全部字段 → 选动作 → 拍照 → 提交</span></div>
    <div class="card">
      <ScanInput placeholder="扫描或输入仓储码 / ERP 单据号 / 库位码" @scan="onScan" />
      <div class="muted tips">
        扫码为主：物料码由仓储系统赋码，签收时扫实物上的仓储条码，一次就把“单、行、物”绑住；一张单据行出了几个码就逐个扫，系统按码分成几条行。
        也可以扫或输入 ERP 单据号，系统列出这张单上的各行，可逐行确认签收；一人领整单时可用<b>整单签收</b>一次确认多行（签收时仍要扫仓储条码，同一套校验）。签收必须本人操作；其他动作替他人登记会留痕并通知持有人。
      </div>
    </div>

    <div v-if="res && res.kind === 'doc'" class="card">
      <h3>
        {{ DOC_TYPES[res.doc.doc_type] || res.doc.doc_type }} <span class="mono">{{ res.doc.no }}</span>
        <el-tag :type="res.doc.posted ? 'success' : 'warning'" size="small" style="margin-left: 6px">{{ res.doc.posted ? '已过账' : '未过账，不能签收' }}</el-tag>
        <el-tag v-if="res.to_sign" type="primary" size="small" style="margin-left: 4px">待我签收 {{ res.to_sign }} 行</el-tag>
        <el-button v-if="res.to_sign > 1 && res.doc.posted" type="primary" size="small" style="margin-left: 8px" @click="openBatch">整单签收（{{ res.to_sign }} 行）</el-button>
      </h3>
      <div v-for="r in res.rows" :key="r.line_no" class="row">
        <div class="rh"><b>第 {{ r.line_no }} 行</b> {{ r.material_code }} {{ r.material }} · {{ r.qty }} {{ r.unit }}</div>
        <div v-if="!r.lines.length" class="muted">没有可见的领用行</div>
        <div v-for="l in r.lines" :key="l.id" class="item">
          <div class="grow-min">
            <router-link :to="`${p}/line/${l.id}`" class="mono">{{ l.code }}</router-link>
            <span class="muted"> · {{ l.holder }} · 结存 {{ l.balance }} {{ l.unit }}</span>
            <el-tag :type="STATUS_TYPE[l.status]" size="small" style="margin-left: 6px">{{ l.status_name }}</el-tag>
          </div>
          <el-button v-if="l.can_sign" size="small" type="primary" :disabled="!res.doc.posted" @click="openSign(l)">确认签收此行</el-button>
          <el-button v-else size="small" @click="$router.push(`${p}/line/${l.id}`)">详情</el-button>
        </div>
      </div>
      <div v-if="res.hidden" class="muted">另有 {{ res.hidden }} 行不在您的查看范围内。</div>
    </div>

    <div v-if="res && res.kind === 'wms'" class="card">
      <h3>仓储条码 <span class="mono">{{ res.code }}</span> <el-tag size="small" :type="res.wms.status === 'OUTBOUND' ? 'success' : 'warning'">{{ res.wms.status_name }}</el-tag></h3>
      <div class="muted" style="margin-bottom: 10px">
        {{ res.wms.material_code }} {{ res.wms.material || '' }} · {{ res.wms.qty }} {{ res.wms.unit || '' }}{{ res.wms.out_doc_no ? ` · 出库单 ${res.wms.out_doc_no}` : '' }}
      </div>
      <div v-for="l in res.candidates" :key="l.id" class="item">
        <div class="grow-min">
          <router-link :to="`${p}/line/${l.id}`" class="mono">{{ l.code }}</router-link>
          <span class="muted"> · {{ l.project }} · 本行 {{ l.qty }} {{ l.unit }}</span>
          <el-tag v-if="!l.qty_match" type="info" size="small" style="margin-left: 6px">本行还需扫其他码</el-tag>
        </div>
        <el-button size="small" type="primary" @click="openSign(l, res.code)">用此码签收</el-button>
      </div>
      <el-empty v-if="!res.candidates.length" :description="res.wms.status === 'OUTBOUND' ? '您名下没有待签收的同物料行' : '该码在仓储系统中不是已出库状态，不能签收'" :image-size="50" />
    </div>

    <div v-if="res && res.kind === 'location'" class="card">
      <h3>{{ res.location.name }} <span class="mono muted">{{ res.location.code }}</span>（在制 {{ res.lines.length }} 行）</h3>
      <div v-for="l in res.lines" :key="l.id" class="item" @click="$router.push(`${p}/line/${l.id}`)">
        <div class="grow-min"><b>{{ l.material }}</b> <span class="mono muted">{{ l.code }}</span><div class="muted">{{ l.project }} · {{ l.holder }}</div></div>
        <span class="num">{{ l.balance }} {{ l.unit }}</span>
      </div>
      <el-empty v-if="!res.lines.length" description="该位置没有在制物料" :image-size="50" />
    </div>

    <el-dialog v-model="batch.visible" title="整单签收" :width="mobile ? '94%' : '560px'" destroy-on-close append-to-body>
      <div class="muted" style="margin-bottom: 8px">单据 <span class="mono">{{ res?.doc?.no }}</span> · 勾选本次一起签收的行（只能签本人名下的行）</div>
      <el-checkbox-group v-model="batch.ids" style="display: block">
        <div v-for="l in signable" :key="l.id" class="item">
          <el-checkbox :value="l.id" />
          <div class="grow-min">第 {{ l.line_no }} 行 · {{ l.material_code }} {{ l.material }} · <b>{{ l.qty }}</b> {{ l.unit }}</div>
          <el-tag v-if="res.sign_needs_codes" size="small" :type="tally(l) === +l.qty ? 'success' : 'warning'">已扫 {{ tally(l) }} / {{ l.qty }}</el-tag>
        </div>
      </el-checkbox-group>
      <template v-if="res?.sign_needs_codes">
        <div style="margin: 10px 0 6px"><b>扫描整单实物上的仓储条码</b>（系统按物料 / 出库行号分到各行，每行合计须等于本行数量）</div>
        <ScanInput placeholder="扫描或输入仓储条码" @scan="addBatchCode" />
        <div v-for="(c, i) in batch.codes" :key="c.barcode" class="code-row">
          <span class="mono">{{ c.barcode }}</span><span class="muted">{{ c.material_code }}{{ c.out_line_no ? ` · 第 ${c.out_line_no} 行` : '' }}</span>
          <span class="num">{{ c.qty }} {{ c.unit || '' }}</span>
          <el-button size="small" text type="danger" @click="batch.codes.splice(i, 1)">移除</el-button>
        </div>
      </template>
      <div style="margin: 10px 0 6px"><b>现场照片（必拍，整单实物同框）</b></div>
      <PhotoPicker v-model="batch.photos" />
      <el-input v-model="batch.note" placeholder="说明（选填）" style="margin-top: 8px" />
      <template #footer>
        <el-button @click="batch.visible = false">取消</el-button>
        <el-button type="primary" :loading="batch.saving" :disabled="!batch.ids.length" @click="submitBatch">确认签收 {{ batch.ids.length }} 行</el-button>
      </template>
    </el-dialog>

    <div v-if="history.length" class="card">
      <div class="muted">最近扫描：<span v-for="h in history" :key="h" class="mono hist" @click="onScan(h)">{{ h }}</span></div>
    </div>

  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import PhotoPicker from '../components/PhotoPicker.vue'
import { DOC_TYPES, STATUS_TYPE } from '../utils'
import ScanInput from '../components/ScanInput.vue'

const route = useRoute()
const router = useRouter()
const mobile = computed(() => route.path.startsWith('/m'))
const p = computed(() => mobile.value ? '/m' : '')
const res = ref(null)
const history = ref([])

async function onScan(code) {
  history.value = [code, ...history.value.filter(h => h !== code)].slice(0, 5)
  const r = await api.get(`/api/scan/${encodeURIComponent(code)}`)
  if (r.kind === 'line') return router.push(`${p.value}/line/${r.line_id}`)
  if (r.kind === 'req') return router.push(`${p.value}/req/${r.req_id}`)
  res.value = r
}
// 顶栏「扫码查找」带过来的单据号 / 条码 / 库位码：打开页面时直接查
onMounted(() => { if (route.query.code) onScan(String(route.query.code)) })
const signable = computed(() => (res.value?.rows || []).flatMap(r => r.lines.filter(l => l.can_sign)
  .map(l => ({ ...l, line_no: r.line_no, material_code: r.material_code, material: r.material }))))
const batch = reactive({ visible: false, ids: [], codes: [], photos: [], note: '', saving: false })
function openBatch() {
  Object.assign(batch, { visible: true, ids: signable.value.map(l => l.id), codes: [], photos: [], note: '', saving: false })
}
// 仅用于显示：按物料（有出库行号时按行号）把已扫的码归到各行；最终以后端核对为准
function tally(l) {
  return +batch.codes.filter(c => c.material_code === l.material_code && (!c.out_line_no || c.out_line_no === l.line_no))
    .reduce((a, c) => a + (+c.qty || 0), 0).toFixed(3)
}
async function addBatchCode(code) {
  if (batch.codes.some(c => c.barcode === code)) return ElMessage.warning(`条码 ${code} 已扫过`)
  const bc = await api.get(`/api/wms/barcodes/${encodeURIComponent(code)}`)
  if (bc.used) return ElMessage.error(`条码 ${code} 已被其他领用行使用`)
  if (bc.status !== 'OUTBOUND') return ElMessage.error(`条码 ${code} 在仓储系统中为「${bc.status_name}」，不能签收`)
  batch.codes.push(bc)
}
async function submitBatch() {
  if (!batch.photos.length) return ElMessage.warning('签收必须拍照留证')
  batch.saving = true
  try {
    const r = await api.post('/api/lines/batch-sign', { line_ids: batch.ids, codes: batch.codes.map(c => c.barcode),
      photos: batch.photos, note: batch.note })
    ElMessage.success(`已签收 ${r.signed} 条领用行`)
    batch.visible = false
    onScan(res.value.code)
  } finally { batch.saving = false }
}
function openSign(l, code = '') {
  // 签收统一在物料详情里做：可连续扫多个仓储条码（一码一行）
  router.push(`${p.value}/line/${l.id}?` + (code ? `sign_code=${encodeURIComponent(code)}` : 'sign=1'))
}
</script>

<style scoped>
.tips { margin-top: 10px; line-height: 1.7; }
.row { border-top: 1px dashed var(--line); padding: 10px 0; }
.row:first-of-type { border-top: 0; }
.rh { font-size: 13px; margin-bottom: 6px; }
.item { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; margin-bottom: 6px; cursor: pointer; }
.grow-min { flex: 1; min-width: 0; font-size: 13px; }
.hist { margin-left: 8px; cursor: pointer; color: var(--brand); }
.code-row { display: flex; gap: 12px; align-items: center; padding: 4px 0; border-bottom: 1px dashed var(--line); font-size: 13px; }
.code-row .num { margin-left: auto; }
</style>
