<template>
  <el-select v-model="model" filterable remote clearable :remote-method="search" :loading="loading"
             :placeholder="placeholder" style="width: 100%" @change="onChange">
    <el-option v-for="o in options" :key="o.id" :value="o.id" :label="label(o)">
      <span>{{ label(o) }}</span>
      <span class="muted" style="margin-left: 8px">{{ hint(o) }}</span>
    </el-option>
  </el-select>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

// kind: project | material | user
const props = defineProps({
  kind: { type: String, required: true },
  placeholder: { type: String, default: '输入关键字搜索' },
  exclude: { type: Array, default: () => [] }
})
const model = defineModel()
const emit = defineEmits(['picked'])
const options = ref([])
const loading = ref(false)
const urls = { project: '/api/projects', material: '/api/materials', user: '/api/users' }

const label = o => props.kind === 'user' ? o.name : `${o.code} ${o.name}`
const hint = o => props.kind === 'project' ? (o.customer_code ? `客户 ${o.customer_code}` : '')
  : props.kind === 'material' ? `${o.spec || ''} / ${o.unit}` : (o.title || o.username)

async function search(q = '') {
  loading.value = true
  try {
    const rows = await api.get(`${urls[props.kind]}?q=${encodeURIComponent(q)}`)
    options.value = rows.filter(r => !props.exclude.includes(r.id))
  } finally { loading.value = false }
}
function onChange(v) { emit('picked', options.value.find(o => o.id === v)) }
onMounted(() => search(''))
defineExpose({ options })
</script>
