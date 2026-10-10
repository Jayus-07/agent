'use client'

/**
 * navConfig — 用户端全局导航配置（单一数据源）
 *
 * 演进记录：
 * - 2026-09-16 二次收敛：用户端只保留「业务对话 + 报告」两类入口；
 *   知识运营/竞品/选品/客服/告警工单等全部归管理端 frontend-admin/
 *   并与后端 API 页面职责分开维护。
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
 * - 2026-09-30 四次追加：新增顶层「旅游规划」→ /travel。
 *   背景：/travel 页面 2026-09-22（P0-5）就已建成（逐日时间轴 + 费用拆分 +
 *   地图打点 + ICS 导出），但从未挂进任何导航 —— 站内点不到，功能等于不存在。
 *   说明：本次追加不违反 09-21 三次收敛的本意 —— 那次收敛要赶走的是
 *   **运营/管理后台**（知识库、竞品、选品、告警工单），而旅游规划是**面向
 *   普通用户的业务功能**，与「AI 对话」同类。且 /travel 建于收敛次日，
 *   当时并不在「被裁撤」范围内。
 *   生效范围：NAV 是用户端导航唯一数据源 —— 全局侧栏（Sidebar）与 /agent
 *   任务模式侧栏（TaskSidebar 的「全部功能」折叠组）均直接 map 本数组，
 *   故加一项即两处同时生效。
 * - 2026-10-01 五次收敛（本文件）：原「AI 对话」**组**（唯一子项「智能问答」，
 *   外加 09-17 加的「智能客服」直达子项）压平为顶层直达「AI 助手」→ /agent。
 *   侧栏仍只维护主助手和业务页面；智能客服从主助手顶栏跳转到独立路由
 *   /customer-service。旧 /agent?cs=1 链接由主助手兼容重定向。
 */
import { Brain, Plane, ShoppingBag } from 'lucide-react'
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
    // 2026-10-01 五次收敛：原「AI 对话」组 + 子项（智能问答 / 智能客服）
    // 压平为顶层直达。智能客服入口仍在 /agent 顶栏胶囊（ChatHeader）。
    icon: <Brain size={18} />, label: 'AI 助手',
    path: '/agent',
  },
  {
    // 2026-09-30 新增：旅游规划独立整页（非对话子项 —— 该页输出结构化行程，
    // 需要逐日时间轴 / 地图 / 导出，塞进对话流会丢结构）。
    icon: <Plane size={18} />, label: '旅游规划',
    path: '/travel',
  },
  {
    // 2026-10-06 多域隔离收官 M4：选品漏斗专属页（第四扇门）。此前选品
    // 只能靠主图对话 prefilter 截流；隔离拍板后「产出作品送专属页」，
    // 漏斗报告的落地页就是这里（主图选品话术 → 引导卡跳本页带参）。
    // 叫「选品漏斗」不叫「智能选品」：后者是管理端评分控制台的既有语义。
    icon: <ShoppingBag size={18} />, label: '选品漏斗',
    path: '/selection-funnel',
  },
]
