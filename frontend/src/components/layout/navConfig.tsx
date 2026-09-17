'use client'

/**
 * navConfig — 用户端全局导航配置（单一数据源）
 *
 * 2026-09-16 二次收敛：用户端只保留「业务对话 + 报告」两类入口；
 * 知识运营/竞品/选品/客服/告警工单等全部归管理端 frontend-admin/
 * （见 docs/2026-09-16-前端拆分计划.md §1）。Sidebar 与 /agent 任务页
 * 的「全部功能」菜单（TaskSidebar）都消费本配置。
 *
 * 2026-09-17：用户端客服入口走 /agent 顶部栏「智能客服」胶囊按钮
 * （ChatHeader onOpenCS → components/cs/CSDrawer.tsx 滑出抽屉），不设独立导航路由；
 * 坐席工作台 /cs/handoff 仅在管理端 frontend-admin。
 *
 * 2026-09-17 UX P1-⑤（X7 收尾）：全局 nav 增「智能客服」直达子项
 * /agent?cs=1 —— /agent 页检测参数自动滑出 CSDrawer。语义区分：
 * 用户端「智能客服」= CSDrawer 消费者入口；管理端坐席工作台
 * /cs/handoff 仍仅 frontend-admin（测试黑名单的 /cs 路由守卫不变）。
 */
import { Bell, Brain, FileText } from 'lucide-react'
import type { ReactNode } from 'react'

export interface NavItem {
  label: string
  path: string
}

export interface NavEntry {
  icon: ReactNode
  label: string
  /** 一级直达页（无子项时） */
  path?: string
  /** 子页面组 */
  items?: NavItem[]
}

export const NAV: NavEntry[] = [
  {
    icon: <Brain size={18} />, label: 'AI 对话',
    items: [
      { label: '智能问答', path: '/agent' },
      { label: '分析任务', path: '/agent/tasks' },
      // UX P1-⑤：客服直达（/agent 检测 cs=1 自动开抽屉，见 app/agent/page.tsx）
      { label: '智能客服', path: '/agent?cs=1' },
    ],
  },
  {
    icon: <FileText size={18} />, label: '报告中心', path: '/reports',
  },
  {
    // 2026-09-17 UX P1（X7）：ADR-001 判归 workspace 的「我的告警」补上导航入口；
    // 用户端为只读视图，工单流转操作留在管理端 frontend-admin /alerts。
    icon: <Bell size={18} />, label: '我的告警', path: '/alerts',
  },
]
