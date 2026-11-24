<template>
  <div class="labels">
    <div class="bar no-print">
      <el-button type="primary" @click="print"><el-icon><Printer /></el-icon>打印</el-button>
      <el-button @click="$router.back()">返回</el-button>
      <span class="muted">标签 50×30mm，可直接发送到标签打印机。二维码内容即物料码原文（已接仓储时为仓储系统发的码）。</span>
    </div>
    <div v-for="x in items" :key="x.code" class="label">
      <img :src="x.qr" />
      <div class="txt">
        <div class="code">{{ x.code }}</div>
        <div>{{ x.material }}</div>
        <div>{{ x.project }} · {{ x.qty }}{{ x.unit }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import QRCode from 'qrcode'
import { api } from '../api'

const route = useRoute()
const items = ref([])
onMounted(async () => {
  const lines = []
  if (route.query.ids) for (const id of String(route.query.ids).split(',')) lines.push(await api.get(`/api/lines/${id}`))
  if (route.query.codes) for (const c of String(route.query.codes).split(',')) lines.push(await api.get(`/api/lines/by-code/${c}`))
  items.value = await Promise.all(lines.map(async l => ({
    code: l.code, material: l.material, project: l.project, qty: l.balance, unit: l.unit,
    qr: await QRCode.toDataURL(l.code, { margin: 0, width: 160 })
  })))
})
const print = () => window.print()
</script>

<style scoped>
.labels { padding: 16px; background: #fff; min-height: 100vh; }
.bar { display: flex; gap: 10px; align-items: center; margin-bottom: 16px; }
.label { display: inline-flex; gap: 8px; align-items: center; width: 50mm; height: 30mm; border: 1px dashed #bbb; padding: 2mm; margin: 0 4px 4px 0;
  page-break-inside: avoid; overflow: hidden; vertical-align: top; }
.label img { width: 22mm; height: 22mm; }
.txt { font-size: 8pt; line-height: 1.3; }
.code { font-family: Consolas, monospace; font-weight: 700; font-size: 9pt; }
@media print {
  .no-print { display: none; }
  .labels { padding: 0; }
  .label { border: none; }
}
</style>
