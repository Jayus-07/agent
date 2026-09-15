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
  { icon: <LayoutDashboard size={18} />, label: '运营总览', path: '/' },
  {
    icon: <Database size={18} />, label: '知识运营',
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
      { label: '智能选品', path: '/selection' },
      { label: '选品决策', path: '/selection-decision' },
      { label: '报告中心', path: '/reports' },
    ],
  },
  {
    icon: <Activity size={18} />, label: '可观测',
    items: [
      { label: '问答追踪', path: '/observability/traces' },
      { label: '网关安全', path: '/observability/gateway' },
      { label: 'Token 用量', path: '/observability/tokens' },
      { label: '告警中心', path: '/observability/alerts' },
    ],
  },
  {
    icon: <ScrollText size={18} />, label: '质量与配置',
    items: [
      { label: 'Prompt 管理', path: '/prompts' },
      { label: '评测结果', path: '/evaluations' },
    ],
  },
  {
    icon: <ShieldCheck size={18} />, label: '运营干预',
    items: [
      { label: '工具审批', path: '/approvals' },
      { label: '客服对话', path: '/cs' },
      { label: '客服会话', path: '/cs/conversations' },
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
