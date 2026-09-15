'use client'

/**
 * navConfig — 用户端全局导航配置（单一数据源）
 *
 * 2026-09-16 二次收敛：用户端只保留「业务对话 + 报告」两类入口；
 * 知识运营/竞品/选品/客服/告警工单等全部归管理端 frontend-admin/
 * （见 docs/2026-09-16-前端拆分计划.md §1）。Sidebar 与 /agent 任务页
 * 的「全部功能」菜单（TaskSidebar）都消费本配置。
 */
import { Brain, FileText } from 'lucide-react'
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
    ],
  },
  {
    icon: <FileText size={18} />, label: '报告中心', path: '/reports',
  },
]
