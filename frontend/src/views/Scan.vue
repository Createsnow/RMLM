<template>
  <div>
    <div v-if="!mobile" class="page-head"><h2>扫码</h2><span class="sub">扫物料码 / ERP 单据号 / 库位码 → 带出全部字段 → 选动作 → 拍照 → 提交</span></div>
    <div class="card">
      <ScanInput placeholder="扫描或输入仓储码 / ERP 单据号 / 库位码" @scan="onScan" />
      <div class="muted tips">
        扫码为主：物料码由仓储系统赋码，签收时扫实物上的仓储条码，一次就把“单、行、物”绑住；一张单据行出了几个码就逐个扫，系统按码分成几条行。
        也可以扫或输入 ERP 单据号，系统列出这张单上的各行，<b>逐行确认签收</b>，没有整单全选（签收时仍要扫仓储条码）。经手人固定为当前登录身份，替他人登记会留痕并通知持有人。
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

    <div v-if="history.length" class="card">
      <div class="muted">最近扫描：<span v-for="h in history" :key="h" class="mono hist" @click="onScan(h)">{{ h }}</span></div>
    </div>

  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
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
</style>
