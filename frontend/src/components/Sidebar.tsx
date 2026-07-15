'use client'

import { Sparkles, PanelLeft, PanelLeftClose, LayoutDashboard, Download, Cog, Database, BookOpen, Brain, Activity } from 'lucide-react'
import NavGroup from './layout/NavGroup'

interface Props { collapsed: boolean; onToggle: () => void }

const NAV = [
  { icon: <LayoutDashboard size={18} />, label: '数据驾驶舱', path: '/' },
  {
    icon: <Download size={18} />, label: '数据接入中心',
    items: [
      { label: '本地文件上传', path: '/data-source' },
      { label: '开源数据集', path: '/data-source/datasets' },
      { label: '开放平台', path: '/data-source/platform' },
      { label: '模拟数据生成', path: '/data-source/generator' },
    ],
  },
  {
    icon: <Cog size={18} />, label: '数据处理中心',
    items: [
      { label: '清洗任务', path: '/data-pipeline' },
      { label: '执行历史', path: '/data-pipeline/history' },
    ],
  },
  { icon: <Database size={18} />, label: '数据资产中心', path: '/data-assets' },
  {
    icon: <BookOpen size={18} />, label: 'RAG 知识库',
    items: [
      { label: '概览', path: '/knowledge' },
      { label: '文档管理', path: '/knowledge/documents' },
      { label: 'Chunk 查看', path: '/knowledge/chunks' },
      { label: '检索测试', path: '/knowledge/playground' },
    ],
  },
  {
    icon: <Brain size={18} />, label: 'AI 分析中心',
    items: [
      { label: '智能问答', path: '/agent' },
      { label: '分析任务', path: '/agent/tasks' },
      { label: '历史报告', path: '/agent/reports' },
    ],
  },
  {
    icon: <Activity size={18} />, label: '可观测中心',
    items: [
      { label: 'Agent Trace', path: '/observability/agent-trace' },
      { label: 'LLM 调用', path: '/observability/llm' },
      { label: 'RAG 指标', path: '/observability/rag' },
    ],
  },
]

export default function Sidebar({ collapsed, onToggle }: Props) {
  if (collapsed) {
    return (
      <aside className="w-0 shrink-0 overflow-hidden md:w-14 md:flex md:flex-col md:items-center md:py-3 glass border-r border-black/5 gap-1">
        <button onClick={onToggle} className="p-2 rounded-lg hover:bg-black/5 transition-colors text-text-secondary" aria-label="展开">
          <PanelLeft size={18} />
        </button>
        {NAV.map(g => (
          <NavGroup key={g.path || g.label} {...g} collapsed={true} />
        ))}
      </aside>
    )
  }

  return (
    <aside className="w-64 shrink-0 flex flex-col glass border-r border-black/5">
      {/* 品牌 */}
      <div className="flex items-center justify-between px-4 py-3.5">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-accent flex items-center justify-center">
            <Sparkles size={14} className="text-white" />
          </div>
          <span className="text-sm font-semibold text-text-primary tracking-tight">电商智能数据平台</span>
        </div>
        <button onClick={onToggle} className="p-1.5 rounded-md hover:bg-black/5 transition-colors text-text-secondary" aria-label="收起">
          <PanelLeftClose size={16} />
        </button>
      </div>

      {/* 导航 */}
      <nav className="flex-1 overflow-y-auto px-3 py-2 space-y-1">
        {NAV.map(g => (
          <NavGroup key={g.path || g.label} {...g} collapsed={false} />
        ))}
      </nav>

      {/* 底部 */}
      <div className="px-4 py-3 border-t border-black/5">
        <p className="text-[10px] text-text-muted leading-relaxed">
          Powered by LangGraph<br />Multi-Agent System
        </p>
      </div>
    </aside>
  )
}
