'use client'

/**
 * navConfig — 管理端全局导航配置（单一数据源）
 *
 * 五组结构（2026-09-21 P5 治理，治理实施方案.md §P5）：
 * 总览 / 知识库 / 业务分析 / 运维监控 / 平台管理。
 * 本轮只改导航信息架构，所有页面 URL 保持原样。
 * （历史：2026-09-16 六组 → 2026-09-21 三端拆分（客服迁出）→ 本轮 5 组收敛）
 */
import {
  Activity, Database, LayoutDashboard, Settings, TrendingUp,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { atLeast, type RoleName } from '@/lib/auth'

export interface NavItem {
  label: string
  path: string
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
    icon: <Database size={18} />, label: '知识库', minRole: 'editor',
    items: [
      { label: '文档入库', path: '/knowledge/documents' },
      { label: '待复核', path: '/knowledge/pending' },
      { label: '入库失败', path: '/knowledge/upload-failures' },
      { label: '词库', path: '/knowledge/keywords' },
      { label: '评测结果', path: '/evaluations' },
      { label: '评测集治理', path: '/evaluations/datasets' },
      { label: '反馈候选', path: '/evaluations/feedback', minRole: 'admin' },
      { label: '文档操作日志', path: '/knowledge/operations' },
    ],
  },
  {
    icon: <TrendingUp size={18} />, label: '业务分析',
    items: [
      { label: '数据查询', path: '/data-explorer', minRole: 'editor' },
      { label: '竞品监控', path: '/competitors' },
      { label: '选品漏斗', path: '/selection-funnel' },
      { label: '选品决策', path: '/selection-decision' },
      { label: '报告中心', path: '/reports' },
    ],
  },
  {
    icon: <Activity size={18} />, label: '运维监控',
    items: [
      { label: '任务中心', path: '/tasks' },
      { label: '问答追踪', path: '/observability/traces' },
      { label: '网关安全', path: '/observability/gateway' },
      { label: 'Token 用量', path: '/observability/tokens' },
      { label: '安全运营', path: '/security' },
      { label: '告警中心', path: '/observability/alerts' },
      { label: '定时任务', path: '/schedules' },
      { label: '库存告警工单', path: '/alerts' },
    ],
  },
  {
    icon: <Settings size={18} />, label: '平台管理', minRole: 'editor',
    items: [
      { label: 'Prompt 管理', path: '/prompts' },
      { label: 'Agent 节点', path: '/agents' },
      { label: 'Skill 能力', path: '/skills' },
      { label: 'Tool 治理', path: '/tools', minRole: 'admin' },
      { label: '资产一致性', path: '/consistency', minRole: 'admin' },
      { label: '发布记录', path: '/releases', minRole: 'admin' },
      { label: '预算策略', path: '/cost-governance/budgets' },
      { label: '模型与供应商', path: '/settings/models', minRole: 'admin' },
      { label: '工具审批', path: '/approvals', minRole: 'admin' },
      { label: '访问控制', path: '/settings/access', minRole: 'admin' },
    ],
  },
]

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
