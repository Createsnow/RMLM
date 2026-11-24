<template>
  <div>
    <div class="bar" :title="`耗 ${line.consumed} / 损 ${line.loss} / 退 ${line.returned} / 废 ${line.scrapped} / 存 ${line.balance}`">
      <i class="u" :style="{ width: w.u }" /><i class="l" :style="{ width: w.l }" /><i class="r" :style="{ width: w.r }" />
      <i class="s" :style="{ width: w.s }" /><i class="b" :style="{ width: w.b }" />
    </div>
    <div v-if="legend" class="bar-legend" style="margin-top: 5px">
      <span><i style="background: #7aa7e0" />耗用 {{ (+line.consumed + +(line.split_out || 0)).toFixed(3).replace(/\.?0+$/, '') }}</span>
      <span><i style="background: #e0b25e" />损耗 {{ line.loss }}</span>
      <span><i style="background: #5fb38a" />退库 {{ line.returned }}</span>
      <span><i style="background: #d9776b" />报废 {{ line.scrapped }}</span>
      <span><i style="background: var(--brand)" />结存 {{ line.balance }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { barParts } from '../utils'

const props = defineProps({ line: { type: Object, required: true }, legend: Boolean })
const w = computed(() => barParts(props.line))
</script>
