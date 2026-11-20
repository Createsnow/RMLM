<template>
  <span class="pt">
    <el-image v-for="(u, i) in urls" :key="i" :src="u" :preview-src-list="urls" :initial-index="i" fit="cover" class="img" preview-teleported />
  </span>
</template>

<script setup>
import { ref, watch } from 'vue'
import { api } from '../api'

const props = defineProps({ ids: { type: Array, default: () => [] } })
const urls = ref([])
watch(() => props.ids, async ids => {
  urls.value = (await Promise.all((ids || []).map(id => api.image(id)))).filter(Boolean)
}, { immediate: true })
</script>

<style scoped>
.pt { display: inline-flex; gap: 4px; flex-wrap: wrap; }
.img { width: 40px; height: 40px; border-radius: 4px; }
</style>
