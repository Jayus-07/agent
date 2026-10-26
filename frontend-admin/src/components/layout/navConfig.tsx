'use client'

/**
 * navConfig — 管理端全局导航配置（单一数据源）
 *
 * 六组结构：总览 / 业务运营 / 内容与质量 / 运行监控 / 能力治理 / 安全运维。
 * 工作台负责承载已合并页面；旧列表 URL 由兼容路由重定向到对应工作台。
 * 同组页面通过 section 表达工作边界，避免业务任务和平台技术对象混在一起。
 */
import {
  Plug,
  Activity, AlertTriangle, Bot, CalendarClock, ChartColumn, CheckCheck, CircleDollarSign,
  ClipboardList, Database, FileCode, FileText, Gauge, GitBranch, KeyRound, LayoutDashboard,
  ListTree, MessagesSquare, Package, Puzzle, Rocket, Settings, Shield, ShieldCheck, TrendingUp, Wrench,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { atLeast, type RoleName } from '@/lib/auth'

export interface NavItem {
  label: string
  path: string
  icon?: ReactNode
  /** 菜单跳转地址；可与 path 不同，例如同一工作台的不同查询视图 */
  href?: string
  /** 兼容旧 URL 的高亮匹配路径，不作为新的侧栏入口展示 */
  activePaths?: string[]
  /** 当前入口对应的查询参数，用于同一路由下不同工作台视图的选中态 */
  activeQuery?: { key: string; value: string; defaultValue?: string }
  /** 同一一级菜单下的二级工作分组（仅用于导航展示） */
  section?: string
  /** 可见所需最低角色（缺省 viewer=所有登录用户）；仅控制 UI 显隐，
   *  真正的权限判定在后端（403 兜底），与 apisix 角色闸同语义 */
  minRole?: RoleName
  /** 部门主管的数据库授权可单独开放该入口，不加入平台角色体系。 */
  allowDepartmentManager?: boolean
}

export interface NavEntry {
  icon: ReactNode
  label: string
  /** 一级直达页（无子项时） */
  path?: string
  /** 子页面组 */
  items?: NavItem[]
  /** 是否在展开时显示二级分组标题；仅用于需要更强信息架构的菜单组 */
  showSections?: boolean
  /** 展开侧栏时以静态分类标题展示，并常驻显示子入口 */
  alwaysOpen?: boolean
  minRole?: RoleName
}

export const NAV: NavEntry[] = [
  { icon: <LayoutDashboard size={18} />, label: '运营总览', path: '/' },
  {
    icon: <TrendingUp size={18} />, label: '业务运营', alwaysOpen: true,
    items: [
      { label: '数据查询', path: '/data-explorer', icon: <ChartColumn size={16} />, minRole: 'editor', section: '业务洞察' },
      { label: '竞品监控', path: '/competitors', icon: <TrendingUp size={16} />, section: '业务洞察' },
      {
        label: '选品工作台', path: '/selection-workbench', icon: <ListTree size={16} />, section: '选品运营',
        activePaths: ['/selection-funnel', '/selection-decision'],
      },
      { label: '报告中心', path: '/reports', icon: <FileText size={16} />, section: '业务产出' },
    ],
  },
  {
    icon: <Database size={18} />, label: '内容与质量', minRole: 'editor', alwaysOpen: true,
    items: [
      {
        label: '知识库工作台', path: '/knowledge/workbench', icon: <Database size={16} />, section: '知识内容',
        activePaths: [
          '/knowledge', '/knowledge/documents', '/knowledge/pending',
          '/knowledge/upload-failures', '/knowledge/keywords', '/knowledge/operations',
        ],
      },
      {
        label: '评测中心', path: '/evaluations/center', icon: <CheckCheck size={16} />, minRole: 'admin', section: '评测治理',
        activePaths: ['/evaluations', '/evaluations/datasets', '/evaluations/feedback'],
      },
    ],
  },
  {
    icon: <Activity size={18} />, label: '运行监控', alwaysOpen: true,
    items: [
      {
        label: '运行总览', path: '/observability/monitoring',
        href: '/observability/monitoring?tab=overview',
        activeQuery: { key: 'tab', value: 'overview', defaultValue: 'overview' },
        icon: <Gauge size={16} />,
        section: '运行状态',
      },
      {
        label: '执行记录与 Trace', path: '/observability/monitoring',
        href: '/observability/monitoring?tab=traces',
        activeQuery: { key: 'tab', value: 'traces' },
        icon: <ListTree size={16} />, activePaths: ['/observability/traces'], section: '运行状态',
      },
      { label: 'Agent 与工作流', path: '/agents', icon: <Bot size={16} /> },
      { label: 'Agent 圆桌', path: '/roundtable', icon: <MessagesSquare size={16} />, minRole: 'admin', section: '服务观测' },
      { label: '任务中心', path: '/tasks', icon: <ClipboardList size={16} />, section: '异步任务' },
      { label: '定时任务', path: '/schedules', icon: <CalendarClock size={16} />, section: '异步任务' },
      {
        label: '网关安全', path: '/observability/monitoring',
        href: '/observability/monitoring?tab=gateway',
        activeQuery: { key: 'tab', value: 'gateway' },
        icon: <Shield size={16} />, activePaths: ['/observability/gateway'], section: '服务观测',
      },
      {
        label: 'Token 用量', path: '/observability/monitoring',
        href: '/observability/monitoring?tab=tokens',
        activeQuery: { key: 'tab', value: 'tokens' },
        icon: <CircleDollarSign size={16} />, activePaths: ['/observability/tokens'], section: '服务观测',
      },
      { label: '选品漏斗历史', path: '/observability/selection-funnel', icon: <GitBranch size={16} />, section: '业务追踪' },
    ],
  },
  {
    icon: <Settings size={18} />, label: '能力治理', minRole: 'editor', alwaysOpen: true,
    items: [
      { label: 'Tool 治理', path: '/tools', icon: <Wrench size={16} />, minRole: 'admin', section: 'Tool 与 Capability' },
      { label: 'MCP 能力中心', path: '/mcp', icon: <Plug size={16} />, minRole: 'admin', section: 'Tool 与 Capability' },
      { label: 'Skill 与 Capability', path: '/skills', icon: <Puzzle size={16} />, section: 'Tool 与 Capability' },
      {
        label: '模型与供应商', path: '/settings/models', icon: <Settings size={16} />, minRole: 'admin', section: '模型与成本',
        activePaths: ['/cost-governance/prices'],
      },
      { label: '预算策略', path: '/cost-governance/budgets', icon: <CircleDollarSign size={16} />, section: '模型与成本' },
      { label: 'Prompt 版本', path: '/prompts', icon: <FileCode size={16} /> },
      { label: '资产一致性检查', path: '/consistency', icon: <CheckCheck size={16} />, minRole: 'admin', section: '治理与发布' },
      { label: '发布记录', path: '/releases', icon: <Rocket size={16} />, minRole: 'admin', section: '治理与发布' },
    ],
  },
  {
    icon: <ShieldCheck size={18} />, label: '安全运维', alwaysOpen: true,
    items: [
      { label: '访问控制', path: '/settings/access', icon: <KeyRound size={16} />, minRole: 'admin', allowDepartmentManager: true, section: '权限与租户' },
      { label: '审批与审计', path: '/approvals', icon: <CheckCheck size={16} />, minRole: 'admin' },
      { label: '安全运营', path: '/security', icon: <ShieldCheck size={16} />, section: '服务健康与告警' },
      { label: '服务健康与告警', path: '/observability/alerts', icon: <AlertTriangle size={16} />, minRole: 'admin', section: '服务健康与告警' },
      { label: '库存告警工单', path: '/alerts', icon: <Package size={16} />, section: '服务健康与告警' },
    ],
  },
]

export function isNavPathActive(
  pathname: string | null | undefined,
  path: string,
  alternatePaths: readonly string[] = [],
): boolean {
  if (!pathname) return false
  return [path, ...alternatePaths].some((candidate) => (
    pathname === candidate || pathname.startsWith(`${candidate}/`)
  ))
}

/** 同一路由承载多个视图时，结合查询参数判断具体入口是否选中。 */
export function isNavItemActive(
  pathname: string | null | undefined,
  item: Pick<NavItem, 'path' | 'activePaths' | 'activeQuery'>,
  search = '',
): boolean {
  const isLegacyPath = item.activePaths?.some((candidate) => isNavPathActive(pathname, candidate)) ?? false
  if (isLegacyPath) return true
  if (!isNavPathActive(pathname, item.path)) return false
  if (!item.activeQuery) return true

  const params = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search)
  const currentValue = params.get(item.activeQuery.key) ?? item.activeQuery.defaultValue
  return currentValue === item.activeQuery.value
}

/** 返回当前路径所属的一级菜单，用于侧栏自动展开当前分组。 */
export function getActiveNavLabel(pathname: string | null | undefined): string | undefined {
  return NAV.find((entry) => {
    if (entry.path) return isNavPathActive(pathname, entry.path)
    return entry.items?.some((item) => isNavPathActive(pathname, item.path, item.activePaths))
  })?.label
}

/**
 * 按当前登录角色过滤后的导航（Sidebar / 页面菜单统一消费这个，
 * 不要直接 map NAV——viewer 直接输 URL 仍可达页面，由后端 403 兜底）。
 *
 * baseline=true：忽略客户端角色，返回无门槛菜单（viewer 基线）。
 * visibleNav() 依赖 localStorage 角色，SSR 时拿不到 —— 若 Sidebar 直接
 * 渲染真实角色，admin 登录后客户端首帧比 SSR 多出图标（如含 <ellipse>
 * 的 Database），整树 hydration 失败（2026-09-16 实测：Next dev 弹
 * 「1 error」+ 4 次 Hydration failed，整页降级纯客户端渲染）。
 * Sidebar 的约定：mount 前 visibleNav(true)（与 SSR 一致），mount 后
 * 再切 visibleNav()。
 */
export function visibleNav(baseline = false, departmentManager = false): NavEntry[] {
  const canSee = (minRole?: RoleName) => baseline ? !minRole : !minRole || atLeast(minRole)
  return NAV.flatMap((entry) => {
    if (!canSee(entry.minRole)) return []
    if (!entry.items) return [entry]

    const items = entry.items.filter((item) => (
      canSee(item.minRole) || (departmentManager && item.allowDepartmentManager)
    ))
    if (items.length === 0) return []
    return [{ ...entry, items }]
  })
}
