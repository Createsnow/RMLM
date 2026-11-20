<template>
  <div>
    <div class="page-head"><h2>本组人员</h2><span class="sub">您的下属（逐级）；被冻结的人由直属上级处理后解冻</span></div>
    <div class="card" style="padding: 0">
      <el-table :data="rows">
        <el-table-column prop="name" label="姓名" width="100" />
        <el-table-column prop="title" label="岗位" />
        <el-table-column label="直属上级" width="100"><template #default="{ row }">{{ nameOf(row.manager_id) }}</template></el-table-column>
        <el-table-column label="状态" width="100"><template #default="{ row }"><el-tag v-if="row.frozen" type="danger" size="small">已冻结</el-tag></template></el-table-column>
        <el-table-column label="操作" width="220"><template #default="{ row }">
          <el-button v-if="row.frozen && row.manager_id === store.user.id" size="small" type="primary" @click="unfreeze(row)">解冻</el-button>
          <el-button size="small" @click="clearance(row)">名下结存</el-button>
        </template></el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { store } from '../store'

const rows = ref([])
const nameOf = id => id === store.user.id ? store.user.name : (rows.value.find(r => r.id === id)?.name || '')
async function load() { rows.value = (await api.get('/api/org/team')).subordinates }
async function unfreeze(u) {
  await ElMessageBox.confirm(`确认 ${u.name} 的超期物料已处理或已约定处理方案，解冻其新领用权限？`)
  await api.post(`/api/users/${u.id}/unfreeze`); ElMessage.success('已解冻'); load()
}
async function clearance(u) {
  const r = await api.get(`/api/users/${u.id}/clearance`)
  ElMessageBox.alert(r.clear ? '名下无结存' : r.lines.map(l => `${l.code}：${l.balance}`).join('<br>'), `${u.name} 名下结存`, { dangerouslyUseHTMLString: true })
}
onMounted(load)
</script>
