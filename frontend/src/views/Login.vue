<template>
  <div class="wrap">
    <div class="box">
      <div class="brand"><span class="logo">R</span>研发物料管家</div>
      <div class="muted" style="margin-bottom: 18px">研发物料全生命周期管理</div>
      <el-form @submit.prevent="login">
        <el-form-item><el-input v-model="f.username" size="large" placeholder="账号" autocomplete="username" /></el-form-item>
        <el-form-item><el-input v-model="f.password" size="large" type="password" show-password placeholder="密码" autocomplete="current-password" @keyup.enter="login" /></el-form-item>
        <el-button type="primary" size="large" style="width: 100%" :loading="loading" @click="login">登录</el-button>
      </el-form>
      <div class="muted" style="margin-top: 14px">账号由系统管理员创建；忘记密码请联系管理员重置。</div>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { setLogin } from '../store'
import { isMobile } from '../utils'

const router = useRouter()
const f = reactive({ username: '', password: '' })
const loading = ref(false)
async function login() {
  loading.value = true
  try {
    const r = await api.post('/api/auth/login', f)
    setLogin(r.token, r.user)
    router.replace(isMobile() ? '/m' : '/')
  } finally { loading.value = false }
}
</script>

<style scoped>
.wrap { min-height: 100vh; display: grid; place-items: center; background: linear-gradient(135deg, #0f2741, #1f5fa8); padding: 16px; }
.box { width: 100%; max-width: 360px; background: #fff; border-radius: 14px; padding: 28px; }
.brand { display: flex; align-items: center; gap: 10px; font-size: 20px; font-weight: 800; }
.logo { width: 34px; height: 34px; border-radius: 8px; color: #fff; background: linear-gradient(135deg, #1f8fff, #0b5cb8); display: grid; place-items: center; }
</style>
