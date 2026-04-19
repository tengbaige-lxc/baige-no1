import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { ElMessage } from 'element-plus'
import NProgress from 'nprogress'
import 'nprogress/nprogress.css'

NProgress.configure({ showSpinner: false })

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/login/index.vue'),
    meta: { hidden: true, title: '登录' },
  },
  {
    path: '/',
    component: () => import('@/components/layout/MainLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/dashboard/index.vue'),
        meta: { title: '仪表盘', icon: 'Odometer' },
      },
      {
        path: 'system',
        name: 'System',
        redirect: '/system/users',
        meta: { title: '系统管理', icon: 'Setting' },
        children: [
          {
            path: 'users',
            name: 'UserManagement',
            component: () => import('@/views/system/users.vue'),
            meta: { title: '用户管理', icon: 'User' },
          },
          {
            path: 'roles',
            name: 'RoleManagement',
            component: () => import('@/views/system/roles.vue'),
            meta: { title: '角色管理', icon: 'UserFilled' },
          },
          {
            path: 'menus',
            name: 'MenuManagement',
            component: () => import('@/views/system/menus.vue'),
            meta: { title: '菜单管理', icon: 'Menu' },
          },
          {
            path: 'logs',
            name: 'LogManagement',
            component: () => import('@/views/system/logs.vue'),
            meta: { title: '操作日志', icon: 'Document' },
          },
        ],
      },
      {
        path: 'message',
        name: 'MessageCenter',
        component: () => import('@/views/message/index.vue'),
        meta: { title: '消息中心', icon: 'Message' },
      },
      {
        path: 'profile',
        name: 'Profile',
        component: () => import('@/views/profile/index.vue'),
        meta: { title: '个人中心', icon: 'User', hidden: true },
      },
      {
        path: 'trading',
        name: 'Trading',
        redirect: '/trading/market',
        meta: { title: '交易中心', icon: 'Money' },
        children: [
          {
            path: 'market',
            name: 'Market',
            component: () => import('@/views/trading/market.vue'),
            meta: { title: '行情面板', icon: 'TrendCharts' },
          },
          {
            path: 'trade',
            name: 'Trade',
            component: () => import('@/views/trading/trade.vue'),
            meta: { title: '下单交易', icon: 'SwitchButton' },
          },
          {
            path: 'orders',
            name: 'Orders',
            component: () => import('@/views/trading/orders.vue'),
            meta: { title: '订单管理', icon: 'DocumentChecked' },
          },
          {
            path: 'balance',
            name: 'Balance',
            component: () => import('@/views/trading/balance.vue'),
            meta: { title: '资金管理', icon: 'Wallet' },
          },
          {
            path: 'exchange',
            name: 'ExchangeConfig',
            component: () => import('@/views/trading/exchange.vue'),
            meta: { title: '交易所配置', icon: 'SetUp' },
          },
        ],
      },
      {
        path: 'strategy',
        name: 'Strategy',
        redirect: '/strategy/list',
        meta: { title: '策略交易', icon: 'Cpu' },
        children: [
          {
            path: 'list',
            name: 'StrategyList',
            component: () => import('@/views/strategy/list.vue'),
            meta: { title: '策略列表', icon: 'List' },
          },
          {
            path: 'logs',
            name: 'StrategyLogs',
            component: () => import('@/views/strategy/logs.vue'),
            meta: { title: '策略日志', icon: 'Document' },
          },
        ],
      },
      {
        path: 'pigeon',
        name: 'Pigeon',
        redirect: '/pigeon/positions',
        meta: { title: '白鸽一号', icon: 'DArrowRight' },
        children: [
          {
            path: 'positions',
            name: 'PigeonPositions',
            component: () => import('@/views/trading/positions.vue'),
            meta: { title: '实时持仓', icon: 'View' },
          },
          {
            path: 'records',
            name: 'PigeonRecords',
            component: () => import('@/views/trading/records.vue'),
            meta: { title: '交易记录', icon: 'DocumentCopy' },
          },
          {
            path: 'performance',
            name: 'PigeonPerformance',
            component: () => import('@/views/trading/performance.vue'),
            meta: { title: '绩效分析', icon: 'TrendCharts' },
          },
          {
            path: 'risk',
            name: 'PigeonRisk',
            component: () => import('@/views/trading/risk.vue'),
            meta: { title: '风控配置', icon: 'Warning' },
          },
          {
            path: 'signals',
            name: 'PigeonSignals',
            component: () => import('@/views/trading/signals.vue'),
            meta: { title: '信号监控', icon: 'Bell' },
          },
          {
            path: 'reduce',
            name: 'PigeonReduce',
            component: () => import('@/views/trading/reduce.vue'),
            meta: { title: '减仓管理', icon: 'Bottom' },
          },
          {
            path: 'short',
            name: 'PigeonShort',
            component: () => import('@/views/trading/short.vue'),
            meta: { title: '做空管理', icon: 'BottomRight' },
          },
          {
            path: 'backtest',
            name: 'PigeonBacktest',
            component: () => import('@/views/trading/backtest.vue'),
            meta: { title: '回测系统', icon: 'DataLine' },
          },
        ],
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('@/views/error/404.vue'),
    meta: { hidden: true, title: '404' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior() {
    return { top: 0 }
  },
})

export { routes }

// Navigation guard
router.beforeEach(async (to, from, next) => {
  NProgress.start()
  const userStore = useUserStore()

  if (to.path === '/login') {
    if (userStore.isLoggedIn) {
      next('/')
    } else {
      next()
    }
    return
  }

  if (!userStore.isLoggedIn) {
    next('/login')
    return
  }

  if (!userStore.userInfo) {
    try {
      await userStore.getProfile()
      next({ ...to, replace: true })
    } catch {
      userStore.logout()
      next('/login')
    }
    return
  }

  next()
})

router.afterEach(() => {
  NProgress.done()
})

export default router
