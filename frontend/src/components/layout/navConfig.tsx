'use client'

/**
 * navConfig — 控制台全局导航配置（单一数据源）
 *
 * Sidebar（控制台框架）渲染入口的唯一来源；后续任何页面需要
 * 业务入口（如菜单/跳转）也从这里取，避免多处硬编码漂移。
 */
import {
  Sparkles, LayoutDashboard, BookOpen, Brain, Activity, FileText, AlertTriangle,
  Clock, TrendingUp, ClipboardCheck, ScrollText, Headphones, BarChart3,
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
  { icon: <LayoutDashboard size={18} />, label: '数据驾驶舱', path: '/' },
  {
    icon: <BookOpen size={18} />, label: 'RAG 知识库',
    items: [
      { label: '概览', path: '/knowledge' },
      { label: '文档管理', path: '/knowledge/documents' },
      { label: '词库管理', path: '/knowledge/keywords' },
    ],
  },
  {
    icon: <Brain size={18} />, label: 'AI 对话',
    items: [
      { label: '智能问答', path: '/agent' },
      { label: '分析任务', path: '/agent/tasks' },
    ],
  },
  {
    icon: <Headphones size={18} />, label: '智能客服',
    items: [
      { label: '客服对话', path: '/cs' },
      { label: '会话管理', path: '/cs/conversations' },
    ],
  },
  {
    icon: <FileText size={18} />, label: '报告中心', path: '/reports',
  },
  {
    icon: <AlertTriangle size={18} />, label: '告警中心', path: '/alerts',
  },
  {
    icon: <TrendingUp size={18} />, label: '竞品监控', path: '/competitors',
  },
  {
    icon: <Sparkles size={18} />, label: '智能选品', path: '/selection',
  },
  {
    icon: <ClipboardCheck size={18} />, label: '选品决策', path: '/selection-decision',
  },
  {
    icon: <Clock size={18} />, label: '定时任务', path: '/schedules',
  },
  {
    icon: <Activity size={18} />, label: '链路追踪',
    items: [
      { label: '问答追踪', path: '/observability/traces' },
      { label: 'Token 用量', path: '/observability/tokens' },
      { label: '文档操作日志', path: '/knowledge/operations' },
    ],
  },
  {
    icon: <ScrollText size={18} />, label: 'Prompt 管理', path: '/prompts',
  },
  {
    icon: <BarChart3 size={18} />, label: '评测结果', path: '/evaluations',
  },
]
