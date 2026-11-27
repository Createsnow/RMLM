<template>
  <div class="wrap">
    <div class="box">
      <div class="brand"><span class="logo">R</span>研发物料管家</div>
      <div class="muted" style="margin-bottom: 18px">{{ switching ? '切换账号：只换本页签，其他页签的账号不受影响' : '研发物料全生命周期管理' }}</div>
      <div v-if="wxBusy" class="wx-wait">正在通过企业微信登录…</div>
      <el-alert v-if="wxError" :title="wxError" type="warning" :closable="false" show-icon style="margin-bottom: 14px" />
      <!-- 本浏览器已登录的其他账号：点一下本页签就用这个账号，不用再输密码 -->
      <div v-if="!wxBusy && others.length" class="accts">
        <div class="accts-title">本浏览器已登录的账号</div>
        <div v-for="a in others" :key="a.uid" class="acct" role="button" tabindex="0" :title="`本页签使用 ${a.name || a.username}`"
          @click="pick(a)" @keyup.enter="pick(a)">
          <span class="av">{{ initial(a) }}</span>
          <span class="acct-text">
            <span class="acct-name">{{ a.name || a.username }}</span>
            <span class="acct-user">账号 {{ a.username }}</span>
          </span>
          <el-button link type="info" size="small" @click.stop="forget(a)">退出</el-button>
        </div>
        <div class="or"><span>或登录其他账号</span></div>
      </div>
      <el-button v-if="wecom && inWeCom && !wxBusy" type="success" size="large" style="width: 100%; margin-bottom: 14px" @click="goWeCom">企业微信登录</el-button>
      <el-form v-show="!wxBusy" @submit.prevent="login">
        <el-form-item><el-input v-model="f.username" size="large" placeholder="账号" autocomplete="username" /></el-form-item>
        <el-form-item><el-input v-model="f.password" size="large" type="password" show-password placeholder="密码" autocomplete="current-password" @keyup.enter="login" /></el-form-item>
        <el-button type="primary" size="large" style="width: 100%" :loading="loading" @click="login">登录</el-button>
      </el-form>
      <el-button v-if="switching && store.token && !wxBusy" style="width: 100%; margin: 10px 0 0" size="large" @click="back">
        返回，继续使用 {{ store.user?.name || '当前账号' }}
      </el-button>
      <div class="muted" style="margin-top: 14px">{{ wecom ? '手机上请从企业微信工作台进入，自动登录；电脑端用账号密码。' : '账号由系统管理员创建；忘记密码请联系管理员重置。' }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { accountChanged, forgetAccount, safeNext, setLogin, store, tokenUid, useAccount } from '../store'
import { isMobile } from '../utils'

const router = useRouter()
const route = useRoute()
const f = reactive({ username: '', password: '' })
const loading = ref(false)
const wecom = ref(false)
const wxBusy = ref(false)
const wxError = ref('')
const inWeCom = /wxwork/i.test(navigator.userAgent)
/** 切换账号（左下角账号菜单「切换账号」「新页签登录其他账号」进来）：本页签要换成另一个账号 */
const switching = computed(() => route.query.switch !== undefined)
/** 可切换的已登录账号（不含本页签正在用的） */
const others = computed(() => store.accounts.filter(a => a.uid !== tokenUid(store.token)))
const initial = a => (a.name || a.username || '?').trim().slice(-2)

function nextPath() {
  return safeNext(route.query.next) || (isMobile() ? '/m' : '/')
}
// 进入系统；本页签换了账号时整页重新加载，免得留着上一个账号的页面和数据
function enter() {
  const next = nextPath()
  if (accountChanged()) location.replace(next)
  else router.replace(next)
}
function back() { router.replace(nextPath()) }
function pick(a) {
  if (useAccount(a.uid)) return enter()
  f.username = a.username
  ElMessage.warning(`${a.name || a.username} 的登录已过期，请重新登录`)
}
async function forget(a) {
  try {
    await ElMessageBox.confirm(`退出账号「${a.name || a.username}」？正在使用这个账号的页签会一起退出，其他账号不受影响。`, '退出账号',
      { confirmButtonText: '退出', cancelButtonText: '取消', type: 'warning' })
  } catch { return }
  forgetAccount(a.uid)
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
    const r = await api.post('/api/auth/login', f, { anon: true })
    setLogin(r.token, r.user)
    enter()
  } finally { loading.value = false }
}

// 企业微信网页授权：跳到企微授权页，回来时地址带 code 和 state
async function goWeCom() {
  wxBusy.value = true
  try {
    const r = await api.get(`/api/auth/wecom/url?next=${encodeURIComponent(nextPath())}`, { anon: true })
    session('rmlm_wx_state', r.state)
    session('rmlm_wx_tried', '1')
    location.href = r.url
  } catch { wxBusy.value = false }
}
async function finishWeCom(code, state) {
  wxBusy.value = true
  const expect = session('rmlm_wx_state')
  session('rmlm_wx_state', null)
  if (!expect || expect !== state) {  // 不是本页发起的授权（缺少或对不上校验码）一律拒绝，防止被诱导登录别人的账号
    wxBusy.value = false
    wxError.value = '登录校验失败，请重新进入'
    return
  }
  try {
    const r = await api.post('/api/auth/wecom', { code }, { silent: true, anon: true })
    session('rmlm_wx_tried', null)
    setLogin(r.token, r.user)
    enter()
  } catch (e) {
    wxBusy.value = false
    wxError.value = e.message
  }
}

onMounted(async () => {
  try { wecom.value = (await api.get('/api/auth/config', { silent: true, anon: true })).wecom } catch { wecom.value = false }
  if (!wecom.value) return
  const { code, state } = route.query
  if (typeof code === 'string' && code) return finishWeCom(code, state)
  // 在企微里打开且本次会话还没试过：自动跳授权（失败一次后不再自动跳，避免来回跳转；切换账号时不自动跳）
  if (inWeCom && !switching.value && !session('rmlm_wx_tried')) goWeCom()
})
</script>

<style scoped>
.wrap { min-height: 100vh; display: grid; place-items: center; background: linear-gradient(135deg, #0f2741, #1f5fa8); padding: 16px; }
.box { width: 100%; max-width: 360px; background: #fff; border-radius: 14px; padding: 28px; }
.brand { display: flex; align-items: center; gap: 10px; font-size: 20px; font-weight: 800; }
.wx-wait { padding: 18px 0; text-align: center; color: #41556d; }
.accts { margin-bottom: 6px; }
.accts-title { font-size: 13px; color: #6b7c93; margin-bottom: 8px; }
.acct { display: flex; align-items: center; gap: 10px; padding: 8px 10px; margin-bottom: 8px; border: 1px solid #e3e9f1; border-radius: 10px;
  cursor: pointer; transition: border-color .15s, background .15s; }
.acct:hover, .acct:focus-visible { border-color: #1f8fff; background: #f4f9ff; outline: none; }
.av { width: 32px; height: 32px; flex: none; border-radius: 50%; background: #e8f1fd; color: #1f5fa8; display: grid; place-items: center;
  font-size: 12.5px; font-weight: 700; }
.acct-text { flex: 1; min-width: 0; display: flex; flex-direction: column; line-height: 1.35; }
.acct-name { font-weight: 600; color: #1d2b3a; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.acct-user { font-size: 12px; color: #8494a7; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.or { display: flex; align-items: center; gap: 10px; margin: 14px 0; color: #9aa8b8; font-size: 12.5px; }
.or::before, .or::after { content: ''; flex: 1; height: 1px; background: #e6ebf1; }
.logo { width: 34px; height: 34px; border-radius: 8px; color: #fff; background: linear-gradient(135deg, #1f8fff, #0b5cb8); display: grid; place-items: center; }
</style>
