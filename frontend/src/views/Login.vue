<template>
  <div class="wrap">
    <div class="box">
      <div class="brand"><span class="logo">R</span>研发物料管家</div>
      <div class="muted" style="margin-bottom: 18px">研发物料全生命周期管理</div>
      <div v-if="wxBusy" class="wx-wait">正在通过企业微信登录…</div>
      <el-alert v-if="wxError" :title="wxError" type="warning" :closable="false" show-icon style="margin-bottom: 14px" />
      <el-button v-if="wecom && inWeCom && !wxBusy" type="success" size="large" style="width: 100%; margin-bottom: 14px" @click="goWeCom">企业微信登录</el-button>
      <el-form v-show="!wxBusy" @submit.prevent="login">
        <el-form-item><el-input v-model="f.username" size="large" placeholder="账号" autocomplete="username" /></el-form-item>
        <el-form-item><el-input v-model="f.password" size="large" type="password" show-password placeholder="密码" autocomplete="current-password" @keyup.enter="login" /></el-form-item>
        <el-button type="primary" size="large" style="width: 100%" :loading="loading" @click="login">登录</el-button>
      </el-form>
      <div class="muted" style="margin-top: 14px">{{ wecom ? '手机上请从企业微信工作台进入，自动登录；电脑端用账号密码。' : '账号由系统管理员创建；忘记密码请联系管理员重置。' }}</div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { safeNext, setLogin } from '../store'
import { isMobile } from '../utils'

const router = useRouter()
const route = useRoute()
const f = reactive({ username: '', password: '' })
const loading = ref(false)
const wecom = ref(false)
const wxBusy = ref(false)
const wxError = ref('')
const inWeCom = /wxwork/i.test(navigator.userAgent)

function nextPath() {
  return safeNext(route.query.next) || (isMobile() ? '/m' : '/')
}
function session(key, val) {
  try {
    if (val === undefined) return sessionStorage.getItem(key)
    if (val === null) sessionStorage.removeItem(key)
    else sessionStorage.setItem(key, val)
  } catch { return null }
}

async function login() {
  loading.value = true
  try {
    const r = await api.post('/api/auth/login', f)
    setLogin(r.token, r.user)
    router.replace(nextPath())
  } finally { loading.value = false }
}

// 企业微信网页授权：跳到企微授权页，回来时地址带 code 和 state
async function goWeCom() {
  wxBusy.value = true
  try {
    const r = await api.get(`/api/auth/wecom/url?next=${encodeURIComponent(nextPath())}`)
    session('rmlm_wx_state', r.state)
    session('rmlm_wx_tried', '1')
    location.href = r.url
  } catch { wxBusy.value = false }
}
async function finishWeCom(code, state) {
  wxBusy.value = true
  const expect = session('rmlm_wx_state')
  session('rmlm_wx_state', null)
  if (expect && expect !== state) {
    wxBusy.value = false
    wxError.value = '登录校验失败，请重新进入'
    return
  }
  try {
    const r = await api.post('/api/auth/wecom', { code }, { silent: true })
    session('rmlm_wx_tried', null)
    setLogin(r.token, r.user)
    router.replace(nextPath())
  } catch (e) {
    wxBusy.value = false
    wxError.value = e.message
  }
}

onMounted(async () => {
  try { wecom.value = (await api.get('/api/auth/config', { silent: true })).wecom } catch { wecom.value = false }
  if (!wecom.value) return
  const { code, state } = route.query
  if (typeof code === 'string' && code) return finishWeCom(code, state)
  // 在企微里打开且本次会话还没试过：自动跳授权（失败一次后不再自动跳，避免来回跳转）
  if (inWeCom && !session('rmlm_wx_tried')) goWeCom()
})
</script>

<style scoped>
.wrap { min-height: 100vh; display: grid; place-items: center; background: linear-gradient(135deg, #0f2741, #1f5fa8); padding: 16px; }
.box { width: 100%; max-width: 360px; background: #fff; border-radius: 14px; padding: 28px; }
.brand { display: flex; align-items: center; gap: 10px; font-size: 20px; font-weight: 800; }
.wx-wait { padding: 18px 0; text-align: center; color: #41556d; }
.logo { width: 34px; height: 34px; border-radius: 8px; color: #fff; background: linear-gradient(135deg, #1f8fff, #0b5cb8); display: grid; place-items: center; }
</style>
