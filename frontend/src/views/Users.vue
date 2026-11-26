<template>
  <div>
    <div class="page-head">
      <h2>用户管理</h2><span class="sub">创建账号时勾选角色；"直属上级"由上下级关系自动得出，不是角色</span>
      <span class="grow" />
      <el-checkbox v-model="inactive" @change="load">含已停用</el-checkbox>
      <el-input v-model="q" placeholder="姓名 / 账号" clearable style="width: 160px" @change="load" />
      <el-button v-if="wx.configured" :loading="syncing" @click="syncContacts"><el-icon><Refresh /></el-icon>同步企微通讯录</el-button>
      <el-upload :show-file-list="false" :http-request="doImport" accept=".xlsx"><el-button><el-icon><Upload /></el-icon>批量导入</el-button></el-upload>
      <el-button type="primary" @click="openNew"><el-icon><Plus /></el-icon>新建账号</el-button>
    </div>
    <div class="card" style="padding: 0">
      <el-table :data="rows" @row-click="edit">
        <el-table-column prop="username" label="账号" width="120" />
        <el-table-column prop="name" label="姓名" width="100" />
        <el-table-column prop="title" label="岗位" />
        <el-table-column prop="emp_no" label="工号" width="90" />
        <el-table-column prop="wecom_userid" label="企微账号" width="120" />
        <el-table-column prop="manager_name" label="直属上级" width="100" />
        <el-table-column label="角色"><template #default="{ row }">
          <el-tag v-for="r in row.role_names" :key="r" size="small" class="flag">{{ r }}</el-tag>
          <el-tag v-if="row.is_manager" size="small" type="success" class="flag">直属上级</el-tag>
        </template></el-table-column>
        <el-table-column label="状态" width="110"><template #default="{ row }">
          <el-tag v-if="!row.active" type="info" size="small">停用</el-tag>
          <el-tag v-if="row.frozen" type="danger" size="small">冻结</el-tag>
        </template></el-table-column>
      </el-table>
    </div>
    <div class="muted">批量导入列：序号 | 岗位 | 姓名 | 直属上级（姓名，顶层填 /）| 账号 | 角色（代码逗号分隔，可空）| 企微 UserID（可空）。初始密码 Rmlm@123456，请提醒本人登录后在“修改密码”里自行修改（系统不强制）。</div>
    <div class="muted">{{ wx.configured ? `企微通讯录每天自动同步人员、岗位、直属上级和离职；角色仍在此勾选。${wx.last_run_at ? '最近同步 ' + wx.last_run_at : '尚未同步'}` : '未配置企业微信：用账号密码登录，消息只在站内待办。' }}</div>
    <el-dialog v-model="dlg" :title="f.id ? '编辑账号' : '新建账号'" width="520px">
      <el-form label-width="90px">
        <el-form-item label="账号" required><el-input v-model="f.username" :disabled="!!f.id" /></el-form-item>
        <el-form-item label="姓名" required><el-input v-model="f.name" /></el-form-item>
        <el-form-item label="岗位"><el-input v-model="f.title" /></el-form-item>
        <el-form-item label="工号"><el-input v-model="f.emp_no" /></el-form-item>
        <el-form-item label="企微账号"><el-input v-model="f.wecom_userid" placeholder="企业微信 UserID，用于免密登录和消息推送" /></el-form-item>
        <el-form-item label="直属上级">
          <el-select v-model="f.manager_id" filterable clearable style="width: 100%">
            <el-option v-for="u in all.filter(x => x.id !== f.id)" :key="u.id" :value="u.id" :label="`${u.name}（${u.username}）`" />
          </el-select>
          <div class="muted">审核人按"直属上级"确定；部门负责人的直属上级请设为研发副总。</div>
        </el-form-item>
        <el-form-item label="角色">
          <el-checkbox-group v-model="f.roles">
            <el-checkbox v-for="r in roles" :key="r.code" :value="r.code" :disabled="r.code === 'USER'">{{ r.name }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item :label="f.id ? '重置密码' : '初始密码'"><el-input v-model="f.password" :placeholder="f.id ? '留空不改' : '留空则为 Rmlm@123456'" /></el-form-item>
        <el-form-item v-if="f.id" label="启用"><el-switch v-model="f.active" />
          <span class="muted" style="margin-left: 8px">停用前名下结存必须清零（离职 / 调岗卡点）</span></el-form-item>
      </el-form>
      <template #footer>
        <el-button v-if="f.id" @click="clearance">离职检查</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { useRefresh } from '../useRefresh'

const rows = ref([])
const all = ref([])
const roles = ref([])
const q = ref('')
const inactive = ref(false)
const dlg = ref(false)
const f = reactive({})
const wx = ref({ configured: false })
const syncing = ref(false)
async function load() {
  rows.value = await api.get(`/api/users?q=${encodeURIComponent(q.value)}&include_inactive=${inactive.value}`)
  all.value = await api.get('/api/users')
}
function openNew() { Object.keys(f).forEach(k => delete f[k]); Object.assign(f, { username: '', name: '', roles: ['USER'], active: true, manager_id: null }); dlg.value = true }
function edit(u) { Object.keys(f).forEach(k => delete f[k]); Object.assign(f, { ...u, roles: [...u.roles], password: '' }); dlg.value = true }
async function save() {
  const body = { username: f.username, name: f.name, title: f.title, emp_no: f.emp_no, manager_id: f.manager_id || null, roles: f.roles, password: f.password || null, active: f.active, wecom_userid: f.wecom_userid || null }
  if (f.id) await api.put(`/api/users/${f.id}`, body)
  else await api.post('/api/users', body)
  ElMessage.success('已保存'); dlg.value = false; load()
}
async function clearance() {
  const r = await api.get(`/api/users/${f.id}/clearance`)
  const detail = [r.text, r.lines.length ? '未清零的行：' + r.lines.map(l => l.code + '（' + l.balance + '）').join('、') : ''].filter(Boolean).join('。')
  ElMessageBox.alert(r.clear ? '名下无结存、无待确认转交、无未开完单的申请，可以办理离职 / 调岗。' : detail, '离职检查')
}
async function doImport({ file }) { const r = await api.upload('/api/users/import', file); ElMessage.success(`新建 ${r.created} 个账号，初始密码 ${r.initial_password}`); load() }
async function syncContacts() {
  syncing.value = true
  try {
    const r = await api.post('/api/admin/wecom/sync-contacts')
    ElMessage.success(`同步完成：新建 ${r.created}，绑定 ${r.bound}，更新 ${r.updated}，停用 ${r.deactivated}` + (r.blocked ? `，${r.blocked} 人离职但名下有结存，已通知直属上级` : ''))
    wx.value = await api.get('/api/admin/wecom/status')
    load()
  } finally { syncing.value = false }
}
useRefresh(load, async () => {
  roles.value = await api.get('/api/roles')
  wx.value = await api.get('/api/admin/wecom/status')
  load()
})
</script>
