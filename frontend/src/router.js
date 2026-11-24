import { createRouter, createWebHistory } from 'vue-router'
import { store } from './store'
import { isMobile } from './utils'

const PcLayout = () => import('./layouts/PcLayout.vue')
const MobileLayout = () => import('./layouts/MobileLayout.vue')
const views = import.meta.glob('./views/**/*.vue')
const v = name => views[`./views/${name}.vue`]

const shared = [
  { path: 'todos', component: v('Todos'), meta: { title: '我的待办' } },
  { path: 'apply', component: v('ReqForm'), meta: { title: '领料申请' } },
  { path: 'reqs', component: v('ReqList'), meta: { title: '领料申请' } },
  { path: 'req/:id', component: v('ReqDetail'), meta: { title: '申请详情' } },
  { path: 'approve', component: v('Approvals'), meta: { title: '待我审核' } },
  { path: 'ledger', component: v('Ledger'), meta: { title: '领用台账' } },
  { path: 'line/:id', component: v('LineDetail'), meta: { title: '物料详情' } },
  { path: 'scan', component: v('Scan'), meta: { title: '扫码' } }
]

const routes = [
  { path: '/login', component: v('Login'), meta: { public: true } },
  { path: '/labels', component: v('Labels'), meta: { title: '打印标签' } },
  {
    path: '/',
    component: PcLayout,
    children: [
      { path: '', component: v('Dashboard'), meta: { title: '总览看板' } },
      ...shared,
      { path: 'team', component: v('Team'), meta: { title: '本组人员' } },
      { path: 'issue', component: v('Issue'), meta: { title: '开单绑定' } },
      { path: 'erp', component: v('ErpDocs'), meta: { title: 'ERP 单据' } },
      { path: 'dispose', component: v('Dispose'), meta: { title: '退库 / 报废' } },
      { path: 'returns', redirect: '/dispose' },
      { path: 'scrap', redirect: '/dispose?tab=scrap' },
      { path: 'history', component: v('History'), meta: { title: '历史基线' } },
      { path: 'recon', component: v('Recon'), meta: { title: '对账与关账' } },
      { path: 'reversals', component: v('Reversals'), meta: { title: '冲销' } },
      { path: 'reports', component: v('Reports'), meta: { title: '报表' } },
      { path: 'master', component: v('Master'), meta: { title: '主数据' } },
      { path: 'dicts', component: v('Dicts'), meta: { title: '字典' } },
      { path: 'users', component: v('Users'), meta: { title: '账号管理' } },
      { path: 'delegates', component: v('Delegates'), meta: { title: '代理审核' } },
      { path: 'audit', component: v('Audit'), meta: { title: '审计日志' } },
      { path: 'password', component: v('Password'), meta: { title: '修改密码' } }
    ]
  },
  {
    path: '/m',
    component: MobileLayout,
    children: [
      { path: '', component: v('m/MHome'), meta: { title: '首页' } },
      { path: 'mine', component: v('m/MMine'), meta: { title: '我的领用' } },
      { path: 'ledger', component: v('m/MLedger'), meta: { title: '台账' } },
      { path: 'password', component: v('Password'), meta: { title: '修改密码' } },
      ...shared
    ]
  },
  { path: '/:pathMatch(.*)*', redirect: '/' }
]

const router = createRouter({ history: createWebHistory(), routes })

router.beforeEach(to => {
  if (!to.meta.public && !store.token) return { path: '/login', query: to.fullPath === '/' ? {} : { next: to.fullPath } }
  if (to.path === '/' && isMobile() && !sessionStorage.getItem('rmlm_pc')) return '/m'
  document.title = `${to.meta.title ? to.meta.title + ' · ' : ''}研发物料管家`
})

export default router
