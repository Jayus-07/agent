'use client'

/**
 * navConfig — 客服端（坐席工作台）全局导航配置（单一数据源）
 *
 * 2026-09-21 三端拆分：客服端 frontend-cs（:3300）从管理端 frontend-admin
 * 拆出，只承载「智能客服」域 —— 坐席接单/对话、会话查询、满意度统计。
 * 平台治理类入口（知识运营/可观测/成本/质量配置/审批/自动化）全部留在
 * 管理端，运营干预中的客服四项不再出现在管理端导航。
 *
 * 三级页面：/cs（工作台首页）、/cs/handoff（人工接入坐席）、
 * /cs/conversations（会话管理 + 详情）、/cs/stats（满意度统计）。
 *
 * 角色说明（MVP 阶段不加 UI 门槛）：本端面向坐席（cs_agents.role=agent）
 * 与客服主管（supervisor/admin）。客服域内部角色已存在于
 * customer_service.cs_agents.role，尚未接入 JWT/网关注入的身份头，
 * 故此处暂不设 minRole；真正的权限判定在后端（403 兜底）。
 * 接线后：满意度统计应对 agent 隐藏（仅 supervisor/admin 可见）。
 */
import {
  BarChart3, Headset, LayoutDashboard, MessagesSquare,
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
  { icon: <LayoutDashboard size={18} />, label: '工作台', path: '/cs' },
  { icon: <Headset size={18} />, label: '人工接入', path: '/cs/handoff' },
  { icon: <MessagesSquare size={18} />, label: '会话管理', path: '/cs/conversations' },
  { icon: <BarChart3 size={18} />, label: '满意度统计', path: '/cs/stats' },
]

/**
 * 按当前登录角色过滤后的导航（Sidebar / 页面菜单统一消费这个，
 * 不要直接 map NAV——viewer 直接输 URL 仍可达页面，由后端 403 兜底）。
 *
 * baseline=true：忽略客户端角色，返回无门槛菜单（viewer 基线）。
 * Sidebar 约定：mount 前 visibleNav(true)（与 SSR 一致），mount 后
 * 再切 visibleNav()，避免 hydration 不一致（2026-09-16 实测教训）。
 */
export function visibleNav(baseline = false): NavEntry[] {
  if (baseline) return NAV.filter((e) => !e.minRole)
  return NAV.filter((e) => !e.minRole || atLeast(e.minRole))
}
