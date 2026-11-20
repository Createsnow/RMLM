<template>
  <div class="pp">
    <div v-for="p in list" :key="p.id" class="thumb">
      <img :src="p.url" />
    </div>
    <label class="add" :class="{ busy }">
      <input type="file" accept="image/*" capture="environment" multiple @change="onPick" :disabled="busy" />
      <el-icon v-if="!busy" :size="22"><Camera /></el-icon>
      <span>{{ busy ? '上传中…' : '拍照' }}</span>
    </label>
    <div class="tip">照片自动加时间和姓名水印，永久保存，不能删除</div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { api } from '../api'

const model = defineModel({ type: Array, default: () => [] })
const list = ref([])
const busy = ref(false)

async function onPick(e) {
  const files = [...e.target.files]
  e.target.value = ''
  busy.value = true
  try {
    for (const f of files) {
      const r = await api.upload('/api/photos', f)
      list.value.push({ id: r.id, url: URL.createObjectURL(f) })
      model.value = [...model.value, r.id]
    }
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.pp { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.thumb, .add { width: 72px; height: 72px; border-radius: 8px; overflow: hidden; border: 1px solid var(--line); }
.thumb img { width: 100%; height: 100%; object-fit: cover; }
.add { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px; cursor: pointer;
  border-style: dashed; color: var(--ink2); font-size: 12px; background: #fafbfc; }
.add input { display: none; }
.add.busy { opacity: .6; }
.tip { width: 100%; font-size: 11px; color: var(--ink2); }
</style>
