'use client'

/**
 * navConfig — 管理端全局导航配置（单一数据源）
 *
 * 六组结构（2026-09-16 Phase 1.5，对应 docs/2026-09-16-前端拆分计划.md §1）：
 * 看得到（可观测）、管得住（知识运营/运营干预/自动化）、查得清（质量与配置）。
 * 业务对话入口（智能问答/报告中心）在用户端 frontend/，这里不放。
 */
import {
  Activity, Clock, Database, LayoutDashboard,
  ScrollText, ShieldCheck, TrendingUp,
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
    icon: <Database size={18} />, label: '知识运营', minRole: 'editor',
    items: [
      { label: '文档入库', path: '/knowledge/documents' },
      { label: '待复核审批', path: '/knowledge/pending' },
      { label: '词库管理', path: '/knowledge/keywords' },
      { label: '文档操作日志', path: '/knowledge/operations' },
    ],
  },
  {
    icon: <TrendingUp size={18} />, label: '业务分析',
    items: [
      { label: '竞品监控', path: '/competitors' },
      { label: '选品漏斗', path: '/selection-funnel' },
      { label: '选品决策', path: '/selection-decision' },
      { label: '报告中心', path: '/reports' },
    ],
  },
  {
    icon: <Activity size={18} />, label: '可观测',
    items: [
      { label: '任务中心', path: '/tasks' },
      { label: '问答追踪', path: '/observability/traces' },
      { label: '网关安全', path: '/observability/gateway' },
      { label: '安全运营', path: '/security' },
      { label: 'Token 用量', path: '/observability/tokens' },
      { label: '告警中心', path: '/observability/alerts' },
    ],
  },
  {
    icon: <ScrollText size={18} />, label: '质量与配置', minRole: 'editor',
    items: [
      { label: 'Prompt 管理', path: '/prompts' },
      { label: 'Agent 节点', path: '/agents' },
      { label: '能力与技能', path: '/skills' },
      { label: '评测结果', path: '/evaluations' },
    ],
  },
  {
    icon: <ShieldCheck size={18} />, label: '运营干预', minRole: 'admin',
    items: [
      { label: '工具审批', path: '/approvals' },
      { label: '客服对话', path: '/cs' },
      { label: '客服会话', path: '/cs/conversations' },
      { label: '人工接入坐席', path: '/cs/handoff' },
      { label: '库存告警工单', path: '/alerts' },
    ],
  },
  {
    icon: <Clock size={18} />, label: '自动化',
    items: [
      { label: '定时任务', path: '/schedules' },
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

