import { store } from './store'
import { api } from './api'

export async function loadDicts(force = false) {
  if (!force && Object.keys(store.dicts).length) return store.dicts
  store.dicts = await api.get('/api/dicts')
  return store.dicts
}

export function dictName(type, code) {
  if (!code) return ''
  const it = (store.dicts[type] || []).find(d => d.code === code)
  return it ? `${code} ${it.name}` : code
}

export function dictOptions(type, filter) {
  return (store.dicts[type] || []).filter(d => d.active && (!filter || filter(d)))
}

export const STATUS_TYPE = {
  S0: 'warning', S1: 'primary', S2: 'primary', S3: 'info', S4: '', S5: 'danger', S6: 'warning', S7: 'success', VOID: 'info'
}

export const REQ_STATUS = { PENDING: ['待审核', 'warning'], APPROVED: ['已通过', 'success'], REJECTED: ['已驳回', 'danger'], CANCELLED: ['已撤回', 'info'] }

export const DOC_TYPES = { LL: '领料出库单', AB: 'A 转 B 单', TL: '退料入库单', BF: '报废出库单', TZ: '调整单' }

export const TODO_KINDS = {
  APPROVE: '审核申请', SIGN: '签收', TRANSFER_CONFIRM: '确认转交', QCONFIRM: '季度在用确认', LINE_APPROVE: '续期/改期审核',
  RETURN_DOC: '退料开单', URGENT_PENDING: '紧急待补', MUST_RETURN: '须全额退库', OVERDUE: '超期处理', UNFREEZE: '解冻',
  RECON_CLAIM: '认领差异', RECON_RESOLVE: '关闭差异', ERP_UNBOUND: '补登', REVERSAL_REVIEW: '冲销复核', LOCKED_LINE: '锁行修正', RENEW_REMIND: '续期提醒',
  PROJECT_CLOSED: '结项清零', CLOSE_REMIND: '关账', DICT_APPROVE: '字典变更', NOTICE: '通知',
  LAB_CONCLUSION: '回填实验结论', TRANSFER_ASSIGN: '外部受让认领', SYNC_FAIL: '同步失败', JOB_FAIL: '定时任务出错',
  ERP_UNPOSTED: '单据反审核'
}

export function todoLink(t, mobile = false) {
  const p = mobile ? '/m' : ''
  if (t.ref_type === 'req') return `${p}/req/${t.ref_id}`
  if (t.ref_type === 'line') return `${p}/line/${t.ref_id}`
  if (t.ref_type === 'event') {
    if (t.kind === 'TRANSFER_CONFIRM') return `${p}/todos?transfer=${t.ref_id}`
    if (t.kind === 'LINE_APPROVE') return `${p}/approve`
    if (t.kind === 'REVERSAL_REVIEW') return '/recon?tab=reversals'
    if (t.kind === 'RETURN_DOC') return '/dispose'
    if (t.kind === 'TRANSFER_ASSIGN') return '/issue'
  }
  if (t.ref_type === 'diff') return '/recon'
  if (t.ref_type === 'erpline') return '/issue'
  if (t.ref_type === 'user') return '/team'
  if (t.ref_type === 'period') return '/recon?tab=close'
  if (t.ref_type === 'dictchange') return '/dicts'
  return null
}

export const fmtMoney = v => (v === null || v === undefined || v === '') ? '-' : Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export const today = (offsetDays = 0) => {
  const d = new Date(Date.now() + offsetDays * 86400000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export const isMobile = () => window.innerWidth < 820

// 结存构成条的各段宽度（耗用 / 损耗 / 退库 / 报废 / 结存；拆出计入耗用段）
export function barParts(l) {
  const u = +l.consumed + +(l.split_out || 0), lo = +l.loss, r = +l.returned, s = +l.scrapped, b = Math.max(+l.balance, 0)
  const tot = u + lo + r + s + b || 1
  const w = n => `${(n / tot * 100).toFixed(1)}%`
  return { u: w(u), l: w(lo), r: w(r), s: w(s), b: w(b) }
}
