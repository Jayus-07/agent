'use client'

/**
 * navConfig — 管理端全局导航配置（单一数据源）
 *
 * 管理端只保留运维/配置入口；业务功能（对话/入库/报告等）在用户端 frontend/。
 * 与用户端 navConfig 保持同一 NavEntry 接口，Sidebar 无需区分。
 */
import {
  Activity, AlertTriangle, BarChart3, Clock, ScrollText,
} from 'lucide-react'
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
    icon: <Activity size={18} />, label: '链路追踪',
    items: [
      { label: '问答追踪', path: '/observability/traces' },
      { label: 'Token 用量', path: '/observability/tokens' },
      { label: '文档操作日志', path: '/knowledge/operations' },
    ],
  },
  {
    icon: <AlertTriangle size={18} />, label: '告警中心', path: '/observability/alerts',
  },
  {
    icon: <ScrollText size={18} />, label: 'Prompt 管理', path: '/prompts',
  },
  {
    icon: <BarChart3 size={18} />, label: '评测结果', path: '/evaluations',
  },
  {
    icon: <Clock size={18} />, label: '定时任务', path: '/schedules',
  },
]
