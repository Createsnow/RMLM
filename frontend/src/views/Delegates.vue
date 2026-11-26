<template>
  <div>
    <div class="page-head"><h2>代理审核</h2><span class="sub">出差 / 请假时设置一名代理人（限同级或上级）；代理期间待审单同时推送给您和代理人，先处理者为准</span></div>
    <div class="card">
      <el-form inline>
        <el-form-item label="代理人"><RemoteSelect v-model="f.agent_id" kind="user" style="width: 180px" /></el-form-item>
        <el-form-item label="时间">
          <el-date-picker v-model="range" type="datetimerange" value-format="YYYY-MM-DDTHH:mm:ss" start-placeholder="开始" end-placeholder="结束" />
        </el-form-item>
        <el-button type="primary" @click="add">设置</el-button>
      </el-form>
    </div>
    <div class="card" style="padding: 0">
      <el-table :data="rows">
        <el-table-column prop="principal" label="被代理人" />
        <el-table-column prop="agent" label="代理人" />
        <el-table-column prop="start_at" label="开始" />
        <el-table-column prop="end_at" label="结束" />
        <el-table-column label="状态" width="160"><template #default="{ row }">
          <el-tag v-if="row.active" type="success" size="small">生效中</el-tag>
          <el-button v-if="row.active && row.principal === store.user.name" size="small" text type="danger" @click="end(row)">提前结束</el-button>
        </template></el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { store } from '../store'
import RemoteSelect from '../components/RemoteSelect.vue'
import { useRefresh } from '../useRefresh'

const rows = ref([])
const f = reactive({ agent_id: null })
const range = ref([])
async function load() { rows.value = await api.get('/api/delegates') }
async function add() {
  await api.post('/api/delegates', { agent_id: f.agent_id, start_at: range.value?.[0], end_at: range.value?.[1] })
  ElMessage.success('已设置'); load()
}
async function end(row) { await api.del(`/api/delegates/${row.id}`); load() }
useRefresh(load)
</script>
