<template>
  <div>
    <el-input v-model="q" placeholder="物料码 / 物料名称" clearable size="small" style="margin-bottom: 8px" @change="load" />
    <el-radio-group v-model="status" size="small" style="margin-bottom: 10px" @change="load">
      <el-radio-button value="active">在制</el-radio-button>
      <el-radio-button value="S5">超期</el-radio-button>
      <el-radio-button value="S6">待单据</el-radio-button>
      <el-radio-button value="">全部</el-radio-button>
    </el-radio-group>
    <div v-for="l in rows" :key="l.id" class="item" @click="$router.push(`/m/line/${l.id}`)">
      <div class="h">
        <div><b>{{ l.material }}</b> <span class="muted">{{ l.material_code }}</span><div class="muted">{{ l.project }} · 客户 {{ l.customer_code || '—' }} · {{ l.holder }}</div></div>
        <el-tag :type="STATUS_TYPE[l.status]" size="small">{{ l.status_name }}</el-tag>
      </div>
      <div style="margin-top: 8px"><BalanceBar :line="l" /></div>
      <div class="r">
        <span>结存 <b style="color: var(--brand)">{{ l.balance }}</b> {{ l.unit }}</span>
        <span :style="{ color: l.overdue ? '#c0392b' : '' }">{{ l.due_date }}</span>
      </div>
    </div>
    <el-empty v-if="!rows.length" description="暂无" :image-size="60" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../../api'
import { STATUS_TYPE } from '../../utils'
import BalanceBar from '../../components/BalanceBar.vue'

const rows = ref([])
const q = ref('')
const status = ref('active')
async function load() { rows.value = await api.get(`/api/lines?scope=visible&status=${status.value}&q=${encodeURIComponent(q.value)}`) }
onMounted(load)
</script>

<style scoped>
.item { background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 12px; margin-bottom: 8px; }
.h { display: flex; justify-content: space-between; gap: 8px; }
.r { display: flex; justify-content: space-between; margin-top: 6px; font-size: 13px; }
</style>
