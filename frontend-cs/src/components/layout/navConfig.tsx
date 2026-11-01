'use client'

/**
 * navConfig — 客服端（坐席工作台）全局导航配置（单一数据源）
 *
 * 2026-09-21 三端拆分：客服端 frontend-cs（:3300）从管理端 frontend-admin
 * 拆出，只承载「智能客服」域 —— 坐席接单/对话、会话查询、满意度统计。
 * 平台治理类入口（知识运营/可观测/成本/质量配置/审批/自动化）全部留在
 * 管理端，运营干预中的客服四项不再出现在管理端导航。
 *
 * 三级页面（P8 切换后七视图，对齐 cs2/nav.ts）：/cs/workspace（工作台）、
 * /cs/conversations（会话管理）、/cs/tickets（工单管理）、/cs/refunds（退款
 * 审核）、/cs/qa（质检中心）、/cs/dashboard（数据看板）、/cs/knowledge（知识助手）。
 *
 * 角色说明（2026-09-21 身份接线完成）：本端面向坐席（cs_agents.role=agent）
 * 与客服主管（supervisor）。客服域角色已接入 JWT roles claim → 网关
 * X-User-Roles → userInfo.csRole，UI 据此显隐；真正的权限判定在后端
 * （403 兜底）与 APISIX /api/cs 角色闸（any_of admin/supervisor/agent）。
 * 满意度统计仅 supervisor 可见（agent 隐藏），其余三项坐席即可见。
 */
import {
  BarChart3, BookOpen, CircleDollarSign, ClipboardCheck, LayoutDashboard,
  MessagesSquare, TicketCheck,
} from 'lucide-react'
import type { ReactNode } from 'react'
import {
  atLeast, atLeastCsRole, type CsRoleName, type RoleName,
} from '@/lib/auth'

export interface NavItem {
  label: string
  path: string
  /** 可见所需最低角色（缺省 viewer=所有登录用户）；仅控制 UI 显隐，
   *  真正的权限判定在后端（403 兜底），与 apisix 角色闸同语义 */
  minRole?: RoleName
  /** 可见所需最低客服域角色（缺省不限）；与 minRole 并列独立判定 */
  minCsRole?: CsRoleName
}

export interface NavEntry {
  icon: ReactNode
  label: string
  /** 一级直达页（无子项时） */
  path?: string
  /** 子页面组 */
  items?: NavItem[]
  minRole?: RoleName
  minCsRole?: CsRoleName
}

export const NAV: NavEntry[] = [
  { icon: <LayoutDashboard size={18} />, label: '工作台', path: '/cs/workspace', minCsRole: 'agent' },
  { icon: <MessagesSquare size={18} />, label: '会话管理', path: '/cs/conversations', minCsRole: 'agent' },
  { icon: <TicketCheck size={18} />, label: '工单管理', path: '/cs/tickets', minCsRole: 'agent' },
  { icon: <CircleDollarSign size={18} />, label: '退款审核', path: '/cs/refunds', minCsRole: 'supervisor' },
  { icon: <ClipboardCheck size={18} />, label: '质检中心', path: '/cs/qa', minCsRole: 'agent' },
  { icon: <BarChart3 size={18} />, label: '数据看板', path: '/cs/dashboard', minCsRole: 'supervisor' },
  { icon: <BookOpen size={18} />, label: '知识助手', path: '/cs/knowledge', minCsRole: 'agent' },
]

/** 单入口可见性：平台角色与客服域角色任一不满足即隐藏 */
function entryVisible(entry: NavEntry | NavItem): boolean {
  return (!entry.minRole || atLeast(entry.minRole))
    && atLeastCsRole(entry.minCsRole)
}

/**
 * 按当前登录角色过滤后的导航（Sidebar / 页面菜单统一消费这个，
 * 不要直接 map NAV——直接输 URL 仍可达页面，由后端 403 兜底）。
 *
 * baseline=true：忽略客户端角色，返回无门槛菜单（未判定基线）。
 * Sidebar 约定：mount 前 visibleNav(true)（与 SSR 一致），mount 后
 * 再切 visibleNav()，避免 hydration 不一致（2026-09-16 实测教训）。
 */
export function visibleNav(baseline = false): NavEntry[] {
  if (baseline) return NAV.filter((e) => !e.minRole && !e.minCsRole)
  return NAV.filter(entryVisible)
}
