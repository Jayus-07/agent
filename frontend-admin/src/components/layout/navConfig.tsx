'use client'

/**
 * navConfig — 管理端全局导航配置（单一数据源）
 *
 * 六组架构：总览 / 业务运营 / 内容与质量 / 运行监控 / 能力治理 / 安全运维。
 * 开启 showSections 二级分区，物归其位：
 * 1. 业务运营：数据探索、报告中心与垂直业务（商品库存预警、客服意图、商旅配置）
 * 2. 内容与质量：知识库工作台与评测中心
 * 3. 运行监控：运行控制中心（聚合总览/Trace/网关/Token）、服务告警与异步调度
 * 4. 能力治理：Tool/MCP/Skill、模型与预算、Prompt与Agent编排、一致性与发布
 * 5. 安全运维：访问控制、审批审计与安全运营防御
 */
import {
  Plug,
  Activity, AlertTriangle, Bot, CalendarClock, ChartColumn, CheckCheck, CircleDollarSign,
  ClipboardList, Database, FileCode, FileText, Gauge, KeyRound, LayoutDashboard,
  MessagesSquare, Package, Puzzle, Rocket, Settings, ShieldCheck, TrendingUp, Wrench,
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
    icon: <TrendingUp size={18} />, label: '业务运营', alwaysOpen: true, showSections: true,
    items: [
      { label: '数据查询', path: '/data-explorer', icon: <ChartColumn size={16} />, minRole: 'editor', section: '业务洞察' },
      { label: '报告中心', path: '/reports', icon: <FileText size={16} />, section: '业务洞察' },
      { label: '商品库存预警', path: '/alerts', icon: <Package size={16} />, section: '垂直业务' },
      { label: '客服意图目录', path: '/capability-inventory', icon: <MessagesSquare size={16} />, minRole: 'admin', section: '垂直业务' },
      { label: '商旅服务扩展', path: '/settings/travel-services', icon: <Rocket size={16} />, minRole: 'admin', section: '垂直业务' },
    ],
  },
  {
    icon: <Database size={18} />, label: '内容与质量', minRole: 'editor', alwaysOpen: true, showSections: true,
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
    icon: <Activity size={18} />, label: '运行监控', alwaysOpen: true, showSections: true,
    items: [
      {
        label: '运行控制中心', path: '/observability/monitoring',
        icon: <Gauge size={16} />,
        section: '服务观测',
        activePaths: [
          '/observability',
          '/observability/traces',
          '/observability/gateway',
          '/observability/tokens',
        ],
      },
      { label: '服务健康与告警', path: '/observability/alerts', icon: <AlertTriangle size={16} />, minRole: 'admin', section: '服务观测' },
      { label: '任务中心', path: '/tasks', icon: <ClipboardList size={16} />, section: '异步与调度' },
      { label: '定时任务', path: '/schedules', icon: <CalendarClock size={16} />, section: '异步与调度' },
    ],
  },
  {
    icon: <Settings size={18} />, label: '能力治理', minRole: 'editor', alwaysOpen: true, showSections: true,
    items: [
      { label: 'Tool 治理', path: '/tools', icon: <Wrench size={16} />, minRole: 'admin', section: 'Tool 与 MCP' },
      { label: '连接中心', path: '/mcp', icon: <Plug size={16} />, minRole: 'admin', section: 'Tool 与 MCP' },
      { label: 'Skill 与 Capability', path: '/skills', icon: <Puzzle size={16} />, section: 'Tool 与 MCP' },
      {
        label: '模型与供应商', path: '/settings/models', icon: <Settings size={16} />, minRole: 'admin', section: '模型与成本',
        activePaths: ['/cost-governance/prices'],
      },
      { label: '预算策略', path: '/cost-governance/budgets', icon: <CircleDollarSign size={16} />, section: '模型与成本' },
      { label: 'Prompt 版本', path: '/prompts', icon: <FileCode size={16} />, section: '编排与提示词' },
      { label: 'Agent 与工作流', path: '/agents', icon: <Bot size={16} />, minRole: 'editor', section: '编排与提示词' },
      { label: 'Agent 圆桌', path: '/roundtable', icon: <MessagesSquare size={16} />, minRole: 'admin', section: '编排与提示词' },
      { label: '资产一致性检查', path: '/consistency', icon: <CheckCheck size={16} />, minRole: 'admin', section: '治理与发布' },
      { label: '发布记录', path: '/releases', icon: <Rocket size={16} />, minRole: 'admin', section: '治理与发布' },
    ],
  },
  {
    icon: <ShieldCheck size={18} />, label: '安全运维', alwaysOpen: true, showSections: true,
    items: [
      { label: '访问控制', path: '/settings/access', icon: <KeyRound size={16} />, minRole: 'admin', allowDepartmentManager: true, section: '权限与租户' },
      { label: '审批与审计', path: '/approvals', icon: <CheckCheck size={16} />, minRole: 'admin', section: '权限与租户' },
      { label: '安全运营', path: '/security', icon: <ShieldCheck size={16} />, section: '安全防御' },
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
