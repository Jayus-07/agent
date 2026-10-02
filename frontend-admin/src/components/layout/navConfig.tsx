'use client'

/**
 * navConfig — 管理端全局导航配置（单一数据源）
 *
 * 五组结构：总览 / 业务运营 / 内容与质量 / 运行中心 / 系统设置。
 * 工作台负责承载已合并页面；旧列表 URL 由兼容路由重定向到对应工作台。
 * 同组页面通过 section 表达工作边界，避免业务任务和平台技术对象混在一起。
 */
import {
  Activity, Database, LayoutDashboard, Settings, TrendingUp,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { atLeast, type RoleName } from '@/lib/auth'

export interface NavItem {
  label: string
  path: string
  /** 兼容旧 URL 的高亮匹配路径，不作为新的侧栏入口展示 */
  activePaths?: string[]
  /** 同一一级菜单下的二级工作分组（仅用于导航展示） */
  section?: string
  /** 可见所需最低角色（缺省 viewer=所有登录用户）；仅控制 UI 显隐，
   *  真正的权限判定在后端（403 兜底），与 apisix 角色闸同语义 */
  minRole?: RoleName
}

export interface NavEntry {
  icon: ReactNode
  label: string
  /** 一级直达页（无子项时） */
  path?: string
  /** 子页面组 */
  items?: NavItem[]
  minRole?: RoleName
}

export const NAV: NavEntry[] = [
  { icon: <LayoutDashboard size={18} />, label: '运营总览', path: '/' },
  {
    icon: <TrendingUp size={18} />, label: '业务运营',
    items: [
      { label: '数据查询', path: '/data-explorer', minRole: 'editor', section: '业务洞察' },
      { label: '竞品监控', path: '/competitors', section: '业务洞察' },
      {
        label: '选品工作台', path: '/selection-workbench', section: '选品运营',
        activePaths: ['/selection-funnel', '/selection-decision'],
      },
      { label: '报告中心', path: '/reports', section: '业务产出' },
    ],
  },
  {
    icon: <Database size={18} />, label: '内容与质量', minRole: 'editor',
    items: [
      {
        label: '知识库工作台', path: '/knowledge/workbench', section: '知识内容',
        activePaths: [
          '/knowledge', '/knowledge/documents', '/knowledge/pending',
          '/knowledge/upload-failures', '/knowledge/keywords', '/knowledge/operations',
        ],
      },
      {
        label: '评测中心', path: '/evaluations/center', minRole: 'admin', section: '评测治理',
        activePaths: ['/evaluations', '/evaluations/datasets', '/evaluations/feedback'],
      },
    ],
  },
  {
    icon: <Activity size={18} />, label: '运行中心',
    items: [
      { label: '任务中心', path: '/tasks', section: '运行状态' },
      {
        label: '运行监控工作台', path: '/observability/monitoring', section: '运行状态',
        activePaths: ['/observability/traces', '/observability/gateway', '/observability/tokens'],
      },
      { label: '安全运营', path: '/security', section: '告警与安全' },
      { label: '告警中心', path: '/observability/alerts', section: '告警与安全' },
      { label: '库存告警工单', path: '/alerts', section: '告警与安全' },
      { label: '定时任务', path: '/schedules', section: '自动化' },
    ],
  },
  {
    icon: <Settings size={18} />, label: '系统设置', minRole: 'editor',
    items: [
      { label: 'Prompt 管理', path: '/prompts', section: 'AI 能力' },
      { label: 'Agent 节点', path: '/agents', section: 'AI 能力' },
      { label: 'Skill 能力', path: '/skills', section: 'AI 能力' },
      { label: 'Tool 治理', path: '/tools', minRole: 'admin', section: 'AI 能力' },
      { label: '资产一致性', path: '/consistency', minRole: 'admin', section: '平台治理' },
      { label: '发布记录', path: '/releases', minRole: 'admin', section: '平台治理' },
      { label: '预算策略', path: '/cost-governance/budgets', section: '平台治理' },
      { label: '模型与供应商', path: '/settings/models', minRole: 'admin', section: '权限与模型' },
      { label: '工具审批', path: '/approvals', minRole: 'admin', section: '权限与模型' },
      { label: '访问控制', path: '/settings/access', minRole: 'admin', section: '权限与模型' },
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
export function visibleNav(baseline = false): NavEntry[] {
  if (baseline) return NAV.filter((e) => !e.minRole)
  return NAV.filter((e) => !e.minRole || atLeast(e.minRole))
}
