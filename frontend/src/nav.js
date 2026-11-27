/**
 * 电脑版导航的唯一来源（顶栏模块、左侧功能栏、工作台常用功能与业务流程都从这里取）。
 *
 * 分三级：
 *   一级 = 模块（顶栏）：工作台 → 领用管理 → 仓库作业 → 对账与报表 → 基础数据 → 系统管理（业务 → 财务 → 基础数据 → 系统管理）
 *   二级 = 功能（左侧栏）：只列当前模块的功能，每项带一句话说明
 *   三级 = 页面内的页签（如 退库与报废 → 退库分摊 / 报废批次）
 * 某个模块里没有可见功能时，整个模块不显示。菜单名称与可见角色见 docs/术语表.md。
 */
import { hasRole, store } from './store'

const approver = () => !!(store.user?.is_manager || (store.user?.delegated_by || []).length)
const manager = () => !!store.user?.is_manager
const roles = (...r) => () => hasRole(...r)
/** 管理、职能岗位：首页显示仓库、对账类数字和验收门槛 */
export const isOps = () => hasRole('KEEPER', 'WAREHOUSE', 'FINANCE', 'ADMIN', 'DEPT_HEAD', 'VP', 'PROJECT_VIEW')

/**
 * 功能项：path 菜单地址；desc 一句话说明（侧栏第二行、工作台卡片）；show 是否可见（默认全部）；
 * kinds 该功能对应的待办类型（侧栏数字）；quick 是否放进工作台「常用功能」（默认否）。
 */
export const MODULES = [
  {
    key: 'work', title: '工作台', icon: 'Monitor', desc: '今天要做的事与常用入口',
    items: [
      { path: '/', name: '首页', icon: 'HomeFilled', desc: '待办、常用功能、业务流程' },
      { path: '/todos', name: '我的待办', icon: 'Bell', desc: '待处理事项与通知', kinds: 'ALL' },
      { path: '/scan', name: '扫码', icon: 'Camera', desc: '签收、使用、暂存、转交' }
    ]
  },
  {
    key: 'use', title: '领用管理', icon: 'Document', desc: '申请、审核与领用台账',
    items: [
      { path: '/reqs', name: '领料申请', icon: 'EditPen', desc: '提交申请、查看审核进度', quick: true },
      { path: '/approve', name: '待我审核', icon: 'Stamp', desc: '审核申请与续期', show: approver, kinds: ['APPROVE', 'LINE_APPROVE'], quick: true },
      { path: '/ledger', name: '领用台账', icon: 'Tickets', desc: '每份物料的结存与流水', quick: true },
      { path: '/team', name: '我的团队', icon: 'User', desc: '下属结存、冻结与解冻', show: manager, kinds: ['UNFREEZE'], quick: true }
    ]
  },
  {
    key: 'wh', title: '仓库作业', icon: 'Box', desc: '开单、ERP 单据、退库与报废',
    items: [
      { path: '/issue', name: '出库开单', icon: 'Sell', desc: '申请与金蝶领料单绑定', show: roles('KEEPER'),
        kinds: ['ERP_UNBOUND', 'TRANSFER_ASSIGN', 'URGENT_PENDING'], quick: true },
      { path: '/erp', name: 'ERP 单据', icon: 'Files', desc: '金蝶单据同步与审核', show: roles('KEEPER', 'FINANCE', 'WAREHOUSE', 'ADMIN'),
        kinds: ['ERP_UNPOSTED'], quick: true },
      { path: '/dispose', name: '退库与报废', icon: 'Van', desc: '退料分摊、报废批次核销', show: roles('KEEPER', 'WAREHOUSE', 'ADMIN'),
        kinds: ['RETURN_DOC'], quick: true }
    ]
  },
  {
    key: 'fin', title: '对账与报表', icon: 'Money', desc: '对账、冲销、关账与报表',
    items: [
      { path: '/recon', name: '对账与关账', icon: 'Finished', desc: '对账差异、冲销、关账', show: roles('KEEPER', 'FINANCE', 'ADMIN', 'DEPT_HEAD', 'VP'),
        kinds: ['RECON_CLAIM', 'RECON_RESOLVE', 'REVERSAL_REVIEW', 'CLOSE_REMIND'], quick: true },
      { path: '/reports', name: '统计报表', icon: 'DataAnalysis', desc: '挂账、项目、损耗、验收', quick: isOps }
    ]
  },
  {
    key: 'base', title: '基础数据', icon: 'Coin', desc: '资料、字典与历史台账',
    items: [
      { path: '/master', name: '基础资料', icon: 'Collection', desc: '项目、物料、库位与箱', show: roles('KEEPER', 'ADMIN', 'WAREHOUSE') },
      { path: '/dicts', name: '数据字典', icon: 'Notebook', desc: '原因、去向等可选项', kinds: ['DICT_APPROVE'] },
      { path: '/history', name: '历史台账', icon: 'Clock', desc: '旧台账导入与总量核对', show: roles('KEEPER', 'ADMIN', 'FINANCE', 'DEPT_HEAD', 'VP', 'WAREHOUSE') }
    ]
  },
  {
    key: 'sys', title: '系统管理', icon: 'Setting', desc: '账号、角色与操作留痕',
    items: [
      { path: '/users', name: '用户管理', icon: 'UserFilled', desc: '账号、角色与直属上级', show: roles('ADMIN'), kinds: ['OFFBOARD', 'SYNC_FAIL', 'JOB_FAIL'], quick: true },
      { path: '/audit', name: '审计日志', icon: 'List', desc: '所有写操作留痕', show: roles('ADMIN') }
    ]
  }
]

/** 不在菜单里的页面：高亮其所属的功能 */
const MENU_OF = { '/req/:id': '/reqs', '/apply': '/reqs', '/line/:id': '/ledger', '/reversals': '/recon' }
export const menuOf = key => MENU_OF[key] || key

/** 个人设置页（头像菜单进入），不属于任何模块 */
export const PERSONAL = { '/password': '修改密码', '/delegates': '代理审核设置' }

const visible = it => !it.show || it.show()
const isQuick = it => typeof it.quick === 'function' ? it.quick() : !!it.quick

/** 某功能上的待办数 */
export function badgeOf(it) {
  const c = store.counts || {}
  if (it.kinds === 'ALL') return (c.tasks || 0) + (c.notices || 0)
  return (it.kinds || []).reduce((n, k) => n + ((c.by_kind || {})[k] || 0), 0)
}

/** 当前用户可见的模块（只含可见功能，带数字）。在 computed 里调用以随登录用户、待办数更新。 */
export function visibleModules() {
  return MODULES.map(m => {
    const items = m.items.filter(visible).map(it => ({ ...it, badge: badgeOf(it) }))
    // 模块上的提醒点只算本模块功能自己的待办（不含「我的待办」总数，避免与铃铛重复）
    const badge = items.reduce((n, it) => n + (it.kinds === 'ALL' ? 0 : it.badge), 0)
    return { ...m, items, badge }
  }).filter(m => m.items.length)
}

/** 页面（页签 key）所属模块 */
export function moduleOf(key, mods = visibleModules()) {
  const p = menuOf(key)
  return mods.find(m => m.items.some(it => it.path === p)) || null
}

/** 能否打开某地址（工作台流程图只给能进的环节加链接） */
export function canOpen(path, mods = visibleModules()) {
  if (path === '/apply' || path === '/scan') return true
  return mods.some(m => m.items.some(it => it.path === path))
}

/** 工作台「常用功能」：本岗位的核心功能，按模块顺序 */
export function quickEntries(mods = visibleModules()) {
  return mods.flatMap(m => m.items.filter(isQuick))
}

/**
 * 工作台「业务流程」：一份物料从申请到关账的七个环节。
 * mine = 当前用户在该环节要动手；paths 按顺序取第一个能打开的地址作为链接。
 */
export const FLOW = [
  { name: '提交申请', who: '使用人', hint: '选项目、物料和数量', paths: ['/apply'], mine: () => true },
  { name: '上级审核', who: '直属上级', hint: '4 小时提醒，24 小时升级', paths: ['/approve'], mine: approver },
  { name: '出库开单', who: '领料专员', hint: '金蝶开单，备注写申请号', paths: ['/issue'], mine: roles('KEEPER') },
  { name: '本人签收', who: '使用人', hint: '扫仓储条码、拍照', paths: ['/scan'], mine: () => true },
  { name: '使用 · 暂存 · 转交', who: '持有人', hint: '在物料详情或扫码登记', paths: ['/ledger'], mine: () => true },
  { name: '退库 · 报废', who: '持有人 → 领料专员', hint: '登记后开退料 / 报废单', paths: ['/dispose', '/ledger'], mine: () => true },
  { name: '对账关账', who: '领料专员 · 财务', hint: '差额为 0 才能关账', paths: ['/recon'], mine: roles('KEEPER', 'FINANCE', 'ADMIN') }
]

export function flowSteps(mods = visibleModules()) {
  return FLOW.map((s, i) => ({ ...s, no: i + 1, mine: s.mine(), path: s.paths.find(p => canOpen(p, mods)) || '' }))
}
