'use client'

/**
 * navConfig — 用户端全局导航配置（单一数据源）
 *
 * 演进记录：
 * - 2026-09-16 二次收敛：用户端只保留「业务对话 + 报告」两类入口；
 *   知识运营/竞品/选品/客服/告警工单等全部归管理端 frontend-admin/
 *   （见 docs/2026-09-16-前端拆分计划.md §1）。
 * - 2026-09-17 UX P1-⑤（X7 收尾）：全局 nav 增「智能客服」直达子项
 *   /agent?cs=1 —— /agent 页检测参数自动滑出 CSDrawer。语义区分：
 *   用户端「智能客服」= CSDrawer 消费者入口；坐席工作台 /cs/handoff
 *   在管理端（2026-09-21 三端拆分起迁往客服端 frontend-cs）。
 * - 2026-09-21 三次收敛（三端拆分）：用户端进一步收敛为「纯 AI 对话」单组。
 *   移除 /agent/tasks、/reports、/alerts 三个页面目录与对应导航项 ——
 *   报告中心归管理端「业务分析」组，库存告警工单归管理端「审批与安全」组。
 *   保留：/agent（智能问答）、/login（登录 + 注册）、memory 记忆会话域、
 *   /agent?cs=1（CSDrawer 智能客服抽屉，非独立路由）。
 *   注意：api 层（src/api/*）整份保留不动 —— surface.test.ts 契约要求所有
 *   域模块共存，未用到的域模块是死代码但无害。
 */
import { Brain } from 'lucide-react'
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
      // UX P1-⑤：客服直达（/agent 检测 cs=1 自动开抽屉，见 app/agent/page.tsx）
      { label: '智能客服', path: '/agent?cs=1' },
    ],
  },
]
