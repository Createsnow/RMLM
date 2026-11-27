<template>
  <div>
    <div class="page-head"><h2>审计日志</h2><span class="sub">所有写操作：操作人、时间、IP、请求内容（密码已脱敏）</span></div>
    <div class="card" style="padding: 0">
      <el-table :data="rows" size="small">
        <el-table-column prop="at" label="时间" width="160" />
        <el-table-column prop="user" label="操作人" width="90" />
        <el-table-column prop="method" label="方法" width="70" />
        <el-table-column prop="path" label="路径" width="260" />
        <el-table-column prop="status" label="结果" width="70" />
        <el-table-column prop="body" label="内容" show-overflow-tooltip />
        <el-table-column prop="ip" label="IP" width="120" />
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { api } from '../api'
import { useRefresh } from '../useRefresh'

const rows = ref([])
useRefresh(async () => { rows.value = await api.get('/api/audit?limit=500') })
</script>
