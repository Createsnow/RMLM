<template>
  <div>
    <div class="page-head">
      <h2 v-if="!embedded">冲销</h2><span class="sub">领料专员在物料详情的事件流水上发起，IT（系统管理员）复核后生效；发起人与复核人不能是同一人</span>
      <span class="grow" />
      <el-radio-group v-model="status" @change="load">
        <el-radio-button value="PENDING">待复核</el-radio-button>
        <el-radio-button value="EFFECTIVE">已生效</el-radio-button>
        <el-radio-button value="REJECTED">已驳回</el-radio-button>
      </el-radio-group>
    </div>
    <div class="card" style="padding: 0">
      <el-table :data="rows">
        <el-table-column prop="id" label="冲销事件号" width="100" />
        <el-table-column label="物料码" width="130"><template #default="{ row }"><router-link :to="`/line/${row.line_id}`" class="mono">{{ row.code }}</router-link></template></el-table-column>
        <el-table-column label="被冲销事件"><template #default="{ row }">#{{ row.target.id }} {{ row.target.etype }} {{ row.target.qty }} · {{ row.target.at }}</template></el-table-column>
        <el-table-column prop="reason" label="原因" />
        <el-table-column prop="operator" label="发起人" width="90" />
        <el-table-column prop="created_at" label="时间" width="160" />
        <el-table-column v-if="status === 'PENDING' && hasRole('ADMIN')" label="复核" width="160"><template #default="{ row }">
          <el-button size="small" type="primary" @click="decide(row, true)">通过</el-button>
          <el-button size="small" @click="decide(row, false)">驳回</el-button>
        </template></el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { hasRole } from '../store'
import { useRefresh } from '../useRefresh'

defineProps({ embedded: Boolean })
const rows = ref([])
const status = ref('PENDING')
async function load() { rows.value = await api.get(`/api/reversals?status=${status.value}`) }
async function decide(row, approve) { await api.post(`/api/reversals/${row.id}/decide`, { approve }); ElMessage.success('已处理'); load() }
useRefresh(load)
</script>
