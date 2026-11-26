<template>
  <div>
    <div class="page-head">
      <h2>退库与报废</h2>
      <span class="sub">多行明细 → 一次匹配到一张 ERP 单；整件报废先入暂存箱、每周按箱批次开单。残料直接按损耗清零，不进这里。</span>
    </div>
    <el-tabs v-model="tab">
      <el-tab-pane label="退库分摊" name="returns"><Returns embedded /></el-tab-pane>
      <el-tab-pane label="报废暂存区 · 批次核销" name="scrap"><Scrap embedded /></el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import Returns from './Returns.vue'
import Scrap from './Scrap.vue'

const route = useRoute()
const tab = ref(route.query.tab === 'scrap' ? 'scrap' : 'returns')
</script>
