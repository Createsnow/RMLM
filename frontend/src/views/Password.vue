<template>
  <div class="card" style="max-width: 420px">
    <h3>修改密码</h3>
    <p class="muted" style="margin: 0 0 12px">改密码后，其他设备上的登录会失效，需要重新登录。通过企业微信自动开通的账号不知道原密码，需要电脑端登录时请联系管理员重置密码。</p>
    <el-form label-position="top">
      <el-form-item label="原密码"><el-input v-model="f.old" type="password" show-password /></el-form-item>
      <el-form-item label="新密码（至少 8 位）"><el-input v-model="f.new" type="password" show-password /></el-form-item>
      <el-form-item label="再输一次"><el-input v-model="again" type="password" show-password /></el-form-item>
      <el-button type="primary" @click="save">保存</el-button>
      <template v-if="$route.path.startsWith('/m')">
        <el-button @click="$router.push('/login?switch=1')">切换账号</el-button>
        <el-button @click="logout">退出登录</el-button>
      </template>
    </el-form>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { logout, setLogin, store } from '../store'
import { closeTab } from '../tabs'

const router = useRouter()
const f = reactive({ old: '', new: '' })
const again = ref('')
async function save() {
  if (f.new !== again.value) return ElMessage.error('两次输入不一致')
  const r = await api.post('/api/auth/password', f)
  // 改密码后其他设备上的旧登录凭证失效，本设备换用新凭证
  if (r.token) setLogin(r.token, { ...store.user, must_change_pw: false })
  ElMessage.success('已修改')
  closeTab('/password', '')
  router.replace(location.pathname.startsWith('/m') ? '/m' : '/')
}
</script>
