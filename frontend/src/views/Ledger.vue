<template>
  <div>
    <div class="page-head">
      <h2>领用台账</h2>
      <span class="sub">一行 = 一份物料的全生命周期，点任意行看详情与事件流水</span>
      <span class="grow" />
      <el-input v-model="q" placeholder="物料码 / 物料名称" clearable style="width: 200px" @change="load" />
      <el-select v-model="scope" style="width: 130px" @change="load">
        <el-option value="visible" label="可见范围" />
        <el-option value="mine" label="我持有的" />
      </el-select>
      <el-button @click="$router.push('/scan')"><el-icon><Camera /></el-icon>扫码登记</el-button>
      <el-button @click="printSel" :disabled="!sel.length"><el-icon><Printer /></el-icon>打印标签</el-button>
    </div>
    <el-radio-group v-model="status" size="small" style="margin-bottom: 12px" @change="load">
      <el-radio-button value="">全部</el-radio-button>
      <el-radio-button value="active">在制</el-radio-button>
      <el-radio-button value="S0">待签收</el-radio-button>
      <el-radio-button value="S5">超期</el-radio-button>
      <el-radio-button value="S3">暂存</el-radio-button>
      <el-radio-button value="S6">待单据</el-radio-button>
      <el-radio-button value="S7">已结案</el-radio-button>
    </el-radio-group>
    <div class="card" style="padding: 0">
      <el-table :data="rows" @selection-change="v => (sel = v)" @row-click="r => $router.push(`/line/${r.id}`)" style="cursor: pointer">
        <el-table-column type="selection" width="40" />
        <el-table-column label="物料码" width="130"><template #default="{ row }"><span class="mono">{{ row.code }}</span><div v-if="row.level === 2" class="muted">子行</div></template></el-table-column>
        <el-table-column label="物料" min-width="160"><template #default="{ row }">{{ row.material }}<div class="muted">{{ row.material_code }} {{ row.spec }}</div></template></el-table-column>
        <el-table-column label="项目" width="120"><template #default="{ row }">{{ row.project }}<div class="muted">客户 {{ row.customer_code || '—' }}</div></template></el-table-column>
        <el-table-column label="领用" width="90" align="right"><template #default="{ row }">{{ row.qty }} {{ row.unit }}</template></el-table-column>
        <el-table-column label="结存构成" width="150"><template #default="{ row }">
          <BalanceBar :line="row" />
          <div class="muted num" style="margin-top: 3px">耗 {{ row.consumed }} / 损 {{ row.loss }} / 退 {{ row.returned }} / 废 {{ row.scrapped }}</div>
        </template></el-table-column>
        <el-table-column label="结存" width="100" align="right"><template #default="{ row }"><b class="num">{{ row.balance }}</b></template></el-table-column>
        <el-table-column label="结存金额" width="100" align="right"><template #default="{ row }">{{ fmtMoney(row.balance_amount) }}</template></el-table-column>
        <el-table-column prop="holder" label="持有人" width="90" />
        <el-table-column label="归还日" width="110"><template #default="{ row }"><span :style="{ color: row.overdue ? '#c0392b' : '' }">{{ row.due_date }}</span></template></el-table-column>
        <el-table-column label="状态" min-width="200"><template #default="{ row }">
          <el-tag :type="STATUS_TYPE[row.status]" size="small">{{ row.status_name }}</el-tag>
          <el-tag v-for="fl in row.flags" :key="fl" size="small" type="info" class="flag">{{ fl }}</el-tag>
        </template></el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { STATUS_TYPE, fmtMoney } from '../utils'
import BalanceBar from '../components/BalanceBar.vue'

const router = useRouter()
const rows = ref([])
const sel = ref([])
const q = ref('')
const scope = ref('visible')
const status = ref('active')
async function load() {
  rows.value = await api.get(`/api/lines?scope=${scope.value}&status=${status.value}&q=${encodeURIComponent(q.value)}`)
}
function printSel() { router.push(`/labels?ids=${sel.value.map(r => r.id).join(',')}`) }
onMounted(load)
</script>
