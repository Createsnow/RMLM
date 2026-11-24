<template>
  <div>
    <div v-if="!mobile" class="page-head"><h2>扫码</h2><span class="sub">扫物料码 / ERP 单据号 / 库位码 → 带出全部字段 → 选动作 → 拍照 → 提交</span></div>
    <div class="card">
      <ScanInput placeholder="扫描或输入仓储码 / ERP 单据号 / 库位码" @scan="onScan" />
      <div class="muted tips">
        扫码为主：物料码取自仓储系统，签收时扫实物上的仓储码，一次就把“单、行、物”绑住。标签破损、未赋码或批量交接时，扫或输入 ERP 单据号，
        系统列出这张单上的各行，<b>逐行确认签收</b>，没有整单全选。经手人固定为当前登录身份，替他人登记会留痕并通知持有人。
      </div>
    </div>

    <div v-if="res && res.kind === 'doc'" class="card">
      <h3>
        {{ DOC_TYPES[res.doc.doc_type] || res.doc.doc_type }} <span class="mono">{{ res.doc.no }}</span>
        <el-tag :type="res.doc.posted ? 'success' : 'warning'" size="small" style="margin-left: 6px">{{ res.doc.posted ? '已过账' : '未过账，不能签收' }}</el-tag>
        <el-tag v-if="res.to_sign" type="primary" size="small" style="margin-left: 4px">待我签收 {{ res.to_sign }} 行</el-tag>
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
      <h3>仓储码 <span class="mono">{{ res.code }}</span> <el-tag size="small">未绑定</el-tag></h3>
      <div class="muted" style="margin-bottom: 10px">
        <template v-if="res.wms.blank">仓储预发的空白码，用于拆分换码：在物料详情里点“拆分换码”时扫这张标签。</template>
        <template v-else>{{ res.wms.material_code }} {{ res.wms.material || '' }}{{ res.wms.qty ? ` · ${res.wms.qty} ${res.wms.unit || ''}` : '' }}{{ res.wms.batch ? ` · 批次 ${res.wms.batch}` : '' }}</template>
      </div>
      <template v-if="!res.wms.blank">
        <div v-for="l in res.candidates" :key="l.id" class="item">
          <div class="grow-min">
            <router-link :to="`${p}/line/${l.id}`" class="mono">{{ l.code }}</router-link>
            <span class="muted"> · {{ l.project }} · {{ l.qty }} {{ l.unit }}</span>
            <el-tag v-if="!l.qty_match" type="danger" size="small" style="margin-left: 6px">数量不符</el-tag>
          </div>
          <el-button v-if="l.action === 'sign'" size="small" type="primary" :disabled="!l.qty_match" @click="openSign(l, res.code)">用此码签收</el-button>
          <el-button v-else size="small" type="warning" :disabled="!l.qty_match" @click="bindTo(l)">补绑到此行</el-button>
        </div>
        <el-empty v-if="!res.candidates.length" description="您名下没有待签收或待绑码的同物料行" :image-size="50" />
      </template>
    </div>

    <div v-if="res && res.kind === 'location'" class="card">
      <h3>{{ res.location.name }} <span class="mono muted">{{ res.location.code }}</span>（在制 {{ res.lines.length }} 行）</h3>
      <div v-for="l in res.lines" :key="l.id" class="item" @click="$router.push(`${p}/line/${l.id}`)">
        <div class="grow-min"><b>{{ l.material }}</b> <span class="mono muted">{{ l.code }}</span><div class="muted">{{ l.project }} · {{ l.holder }}</div></div>
        <span class="num">{{ l.balance }} {{ l.unit }}</span>
      </div>
      <el-empty v-if="!res.lines.length" description="该位置没有在制物料" :image-size="50" />
    </div>

    <div v-if="history.length" class="card">
      <div class="muted">最近扫描：<span v-for="h in history" :key="h" class="mono hist" @click="onScan(h)">{{ h }}</span></div>
    </div>

    <el-dialog v-model="sdlg" title="确认签收" :width="mobile ? '94%' : '460px'" append-to-body>
      <div v-if="signLine" class="muted" style="margin-bottom: 10px">
        <b>{{ signLine.material }}</b> <span class="mono">{{ signLine.code }}</span> · {{ signLine.qty }} {{ signLine.unit }}<br />
        必须本人签收，并拍照留证（实物与标签同框）。
      </div>
      <el-form label-position="top">
        <el-form-item v-if="!noCode" label="仓储码">
          <el-input v-model="signCode" placeholder="扫实物上的仓储标签" clearable />
        </el-form-item>
        <el-checkbox v-model="noCode">无法扫码</el-checkbox>
        <el-form-item v-if="noCode" label="原因" style="margin-top: 6px">
          <el-radio-group v-model="noCodeReason"><el-radio-button v-for="r in ['标签破损', '未赋码', '批量交接']" :key="r" :value="r">{{ r }}</el-radio-button></el-radio-group>
        </el-form-item>
        <el-form-item label="现场照片（必拍）"><PhotoPicker v-model="photos" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="sdlg = false">取消</el-button><el-button type="primary" :loading="saving" @click="sign">确认签收</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { DOC_TYPES, STATUS_TYPE } from '../utils'
import ScanInput from '../components/ScanInput.vue'
import PhotoPicker from '../components/PhotoPicker.vue'

const route = useRoute()
const router = useRouter()
const mobile = computed(() => route.path.startsWith('/m'))
const p = computed(() => mobile.value ? '/m' : '')
const res = ref(null)
const history = ref([])
const sdlg = ref(false)
const signLine = ref(null)
const photos = ref([])
const saving = ref(false)
const signCode = ref('')
const noCode = ref(false)
const noCodeReason = ref(null)

async function onScan(code) {
  history.value = [code, ...history.value.filter(h => h !== code)].slice(0, 5)
  const r = await api.get(`/api/scan/${encodeURIComponent(code)}`)
  if (r.kind === 'line') return router.push(`${p.value}/line/${r.line_id}`)
  if (r.kind === 'req') return router.push(`${p.value}/req/${r.req_id}`)
  res.value = r
}
function openSign(l, code = '') {
  signLine.value = l; photos.value = []; signCode.value = code; noCode.value = false; noCodeReason.value = null; sdlg.value = true
}
async function bindTo(l) {
  await api.post(`/api/lines/${l.id}/bind-code`, { code: res.value.code })
  ElMessage.success(`已把 ${res.value.code} 绑定到本行`)
  router.push(`${p.value}/line/${l.id}`)
}
async function sign() {
  saving.value = true
  try {
    await api.post(`/api/lines/${signLine.value.id}/sign`, { photos: photos.value, code: noCode.value ? null : signCode.value,
      no_code_reason: noCode.value ? noCodeReason.value : null })
    if (res.value.kind === 'wms') { sdlg.value = false; ElMessage.success('已签收并绑定仓储码'); return router.push(`${p.value}/line/${signLine.value.id}`) }
    ElMessage.success(`已签收 ${signLine.value.code}`)
    sdlg.value = false
    res.value = await api.get(`/api/scan/${encodeURIComponent(res.value.code)}`)
  } finally { saving.value = false }
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
</style>
