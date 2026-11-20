<template>
  <div>
    <div v-if="!mobile" class="page-head"><h2>扫码</h2><span class="sub">扫物料码 → 带出全部字段 → 选动作 → 拍照 → 提交</span></div>
    <div class="card">
      <ScanInput @scan="onScan" />
    </div>
    <div v-if="last" class="card">
      <div class="muted">最近一次：{{ last }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import ScanInput from '../components/ScanInput.vue'

const route = useRoute()
const router = useRouter()
const mobile = computed(() => route.path.startsWith('/m'))
const last = ref('')
async function onScan(code) {
  last.value = code
  const line = await api.get(`/api/lines/by-code/${encodeURIComponent(code)}`)
  router.push(`${mobile.value ? '/m' : ''}/line/${line.id}`)
}
</script>
