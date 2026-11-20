<template>
  <div>
    <el-radio-group v-model="status" size="small" style="margin-bottom: 10px" @change="load">
      <el-radio-button value="active">在制</el-radio-button>
      <el-radio-button value="S0">待签收</el-radio-button>
      <el-radio-button value="S5">超期</el-radio-button>
      <el-radio-button value="">全部</el-radio-button>
    </el-radio-group>
    <div v-for="l in rows" :key="l.id" class="item" @click="$router.push(`/m/line/${l.id}`)">
      <div class="h">
        <div><b>{{ l.material }}</b><div class="muted mono">{{ l.code }} · {{ l.project }}</div></div>
        <el-tag :type="STATUS_TYPE[l.status]" size="small">{{ l.status_name }}</el-tag>
      </div>
      <div class="r">
        <span>领 {{ l.qty }} / 存 <b style="color: var(--brand)">{{ l.balance }}</b> {{ l.unit }}</span>
        <span :style="{ color: l.overdue ? '#c0392b' : '' }">{{ l.due_date }}</span>
      </div>
      <div v-if="l.flags.length"><el-tag v-for="fl in l.flags" :key="fl" size="small" type="warning" class="flag">{{ fl }}</el-tag></div>
    </div>
    <el-empty v-if="!rows.length" description="暂无" :image-size="60" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../../api'
import { STATUS_TYPE } from '../../utils'

const rows = ref([])
const status = ref('active')
async function load() { rows.value = await api.get(`/api/lines?scope=mine&status=${status.value}`) }
onMounted(load)
</script>

<style scoped>
.item { background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 12px; margin-bottom: 8px; }
.h { display: flex; justify-content: space-between; gap: 8px; }
.r { display: flex; justify-content: space-between; margin-top: 6px; font-size: 13px; }
</style>
