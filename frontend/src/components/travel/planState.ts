/**
 * components/travel/planState.ts — 旅游页的状态规则（纯函数，直测）
 *
 * 为什么单独抽出来：旅游页有**两条**下规划请求的通道 ——
 *   1. 表单「生成行程」（= 开一份新行程，轮换会话线程）
 *   2. 右侧「对话改行程」抽屉（= 在当前行程上继续改，复用线程）
 * 两条通道的回复合并规则必须一致：**后端只回追问/失败时，不能把已经
 * 展示出来的行程清掉**（原 page.tsx 直接 `setPlan(data)`，一次追问就把
 * 用户刚拿到的行程顶没了）。规则放这里，页面与抽屉共用一份，可直测。
 */
import type { PlanResponse } from '@/api/travel'

export interface PlanState {
  /** 当前展示的行程；null = 还没出过行程 */
  plan: PlanResponse | null
  /** 非行程结果提示（需要补信息 / 规划失败）；**不顶掉**已展示的行程 */
  notice: string
}

export const EMPTY_PLAN_STATE: PlanState = { plan: null, notice: '' }

export interface PlanFormInput {
  destination: string
  days: string
  startDate: string
  partySize: string
  budget: string
  pace: string
  preferences: string[]
  extra: string
}

const PACE_SENTENCE: Record<string, string> = {
  relaxed: '节奏轻松点',
  intense: '排紧凑些',
  moderate: '节奏适中',
}

/**
 * 表单 → 域图消息。字段顺序固定（目的地 → 天数 → 出发 → 人数 → 预算 →
 * 节奏 → 偏好 → 其他）：这批字段在「方案相同」的前提下消息相同，方便复现
 * 问题；空字段整段不出现，不留 `，` 空串（空串会让域图误以为用户表达了空值）。
 */
export function composePlanMessage(input: PlanFormInput): string {
  const parts: string[] = []
  parts.push(input.destination.trim() || '帮我推荐个地方规划行程')
  if (input.days) parts.push(`${input.days}天`)
  if (input.startDate) parts.push(`${input.startDate}出发`)
  if (input.partySize) parts.push(`${input.partySize}个人`)
  if (input.budget) parts.push(`预算${input.budget}元`)
  if (input.pace) parts.push(PACE_SENTENCE[input.pace] ?? '节奏适中')
  if (input.preferences.length) parts.push(`喜欢${input.preferences.join('、')}`)
  if (input.extra.trim()) parts.push(input.extra.trim())
  return parts.join('，')
}

/**
 * 合并一次规划回复。
 * - 回复带行程 → 替换展示（版本号/逐日内容都是新的）
 * - 回复不带行程（追问 / 失败）→ **保留旧行程**，只把这句话放进 notice
 */
export function applyPlanResponse(prev: PlanState, data: PlanResponse): PlanState {
  if (data.itinerary) return { plan: data, notice: '' }
  const text = (data.final_answer || data.clarification || '').trim()
  return { plan: prev.plan, notice: text || '这次没能生成行程，换个说法再试一次' }
}

/** 助手回复的小标签文案（抽屉里给用户「到底改没改」的即时反馈）。 */
export function describePlanReply(data: PlanResponse): { tag: string; tone: 'ok' | 'warn' } {
  if (data.itinerary) {
    return { tag: `行程已更新 · v${data.itinerary.plan_version}`, tone: 'ok' }
  }
  if (data.status === 'failed') return { tag: '规划失败', tone: 'warn' }
  return { tag: '需要补充信息', tone: 'warn' }
}

// ── 会话线程（conversation_id = 后端 checkpoint 的 thread_id） ──
//
// 页面原本每次挂载都 `useRef(sessionId())` 现生成一个 id —— 注释写着
// 「刷新后仍可继续改」，实际刷新即变新线程：用户看到的行程属于旧线程，
// 一句「改成 3 天」会被后端当成全新需求从零规划，旧行程的约束（必去/
// 忌口/预算）全丢。改用 sessionStorage 记住，刷新同一标签页仍接着改。
// 换新行程才轮换（rotate）。

const CONVERSATION_KEY = 'travel:conversation'

export function newConversationId(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  return `t-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

function persist(id: string): void {
  if (typeof window === 'undefined') return
  try {
    window.sessionStorage.setItem(CONVERSATION_KEY, id)
  } catch {
    // 隐私模式/禁用 storage：退化为「每次进页面新线程」，不影响主流程
  }
}

/** 读取当前线程 id；没有（首次进页/刷新后丢失）则新建一个并落盘。 */
export function readConversationId(): string {
  if (typeof window !== 'undefined') {
    try {
      const saved = window.sessionStorage.getItem(CONVERSATION_KEY)
      if (saved) return saved
    } catch {
      /* 见 persist 注释 */
    }
  }
  return rotateConversationId()
}

/** 轮换线程（= 开一份新行程），返回新 id 并落盘。 */
export function rotateConversationId(): string {
  const id = newConversationId()
  persist(id)
  return id
}

// ── 展示小工具 ────────────────────────────────────────────────

export const PACE_LABEL: Record<string, string> = {
  '': '适中',
  relaxed: '轻松',
  intense: '紧凑',
  moderate: '适中',
}

export function formatDayDate(dayDate: string | null): string {
  if (!dayDate) return '未定日期'
  const wd = '日一二三四五六'[new Date(`${dayDate}T00:00:00`).getDay()]
  return `${dayDate} 周${wd}`
}

/** 行程合计（cost.total 是后端 @property，JSON 里可能没有 → 前端求和兜底）。 */
export function itineraryTotal(cost: { tickets?: number; meals?: number; lodging?: number; transit?: number; total?: number } | undefined): number {
  if (!cost) return 0
  if (typeof cost.total === 'number') return cost.total
  return (cost.tickets ?? 0) + (cost.meals ?? 0) + (cost.lodging ?? 0) + (cost.transit ?? 0)
}
