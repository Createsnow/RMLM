<template>
  <div>
    <div class="page-head">
      <h2>领料申请</h2>
      <el-radio-group v-model="scope" @change="load">
        <el-radio-button value="mine">我的申请</el-radio-button>
        <el-radio-button value="all">可见范围</el-radio-button>
      </el-radio-group>
      <el-input v-model="q" placeholder="申请号" clearable style="width: 200px" @change="load" />
      <span class="grow" />
      <el-button type="primary" @click="$router.push('/apply')"><el-icon><Plus /></el-icon>新建申请</el-button>
    </div>
    <div class="card" style="padding: 0">
      <el-table :data="rows" @row-click="r => $router.push(`/req/${r.id}`)" style="cursor: pointer">
        <el-table-column prop="no" label="申请号" width="170" />
        <el-table-column label="状态" width="90"><template #default="{ row }"><el-tag :type="REQ_STATUS[row.status][1]" size="small">{{ REQ_STATUS[row.status][0] }}</el-tag></template></el-table-column>
        <el-table-column prop="user" label="使用人" width="90" />
        <el-table-column label="项目" width="140"><template #default="{ row }">{{ row.project }}</template></el-table-column>
        <el-table-column label="原因" width="110"><template #default="{ row }">{{ dictName('REASON', row.reason_code) }}</template></el-table-column>
        <el-table-column prop="remark" label="备注" min-width="160" show-overflow-tooltip />
        <el-table-column label="物料" min-width="160"><template #default="{ row }">{{ row.items.map(i => `${i.material}×${i.qty}`).join('，') }}</template></el-table-column>
        <el-table-column label="标记" width="120"><template #default="{ row }">
          <el-tag v-if="row.urgent" type="danger" size="small" class="flag">紧急</el-tag>
          <el-tag v-if="row.long_cycle" size="small" class="flag">长周期</el-tag>
        </template></el-table-column>
        <el-table-column prop="approver" label="审核人" width="90" />
        <el-table-column prop="created_at" label="提交时间" width="160" />
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { REQ_STATUS, dictName } from '../utils'

const scope = ref('mine')
const q = ref('')
const rows = ref([])
async function load() { rows.value = await api.get(`/api/requisitions?scope=${scope.value}&q=${encodeURIComponent(q.value)}`) }
onMounted(load)
</script>
