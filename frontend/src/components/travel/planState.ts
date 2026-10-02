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
  /** 已生成但尚未应用的草案；不能替换当前展示行程 */
  pending: PlanResponse | null
  /** 非行程结果提示（需要补信息 / 规划失败）；**不顶掉**已展示的行程 */
  notice: string
}

export const EMPTY_PLAN_STATE: PlanState = { plan: null, pending: null, notice: '' }

export interface PlanFormInput {
  destination: string
  origin?: string
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
  if (input.origin?.trim()) parts.push(`从${input.origin.trim()}出发`)
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
  if (data.itinerary) return { plan: data, pending: null, notice: '' }
  const text = (data.final_answer || data.clarification || '').trim()
  return { plan: prev.plan, pending: prev.pending, notice: text || '这次没能生成行程，换个说法再试一次' }
}

/** 生成修改草案：主内容继续显示当前行程，直到用户明确点击应用。 */
export function previewPlanResponse(prev: PlanState, data: PlanResponse): PlanState {
  if (data.itinerary) return { plan: prev.plan, pending: data, notice: '' }
  const text = (data.final_answer || data.clarification || '').trim()
  return { plan: prev.plan, pending: prev.pending, notice: text || '这次没能生成行程，换个说法再试一次' }
}

export function clearPendingPlan(prev: PlanState): PlanState {
  return { ...prev, pending: null }
}

/**
 * 助手文本的最后一道数据安全兜底。
 *
 * 后端升级或容器滚动期间，旧实例可能仍返回「seed:local / 费用预估 /
 * 置信度」等历史文案。结构化行程卡已经按字段拦截这些值，但聊天气泡也
 * 必须遵守同一规则，不能让旧回复把占位数据重新展示给用户。
 */
export function sanitizeTravelReply(text: string): string {
  if (!text) return text
  const lines = text.split('\n')
  const kept: string[] = []
  let hiddenSection = false
  let removedUnverifiedText = false

  for (const line of lines) {
    const heading = line.match(/^\s{0,3}#{1,6}\s+(.+?)\s*$/)?.[1] ?? ''
    if (heading) {
      if (/费用预估|费用明细|数据来源/.test(heading)) {
        hiddenSection = true
        removedUnverifiedText = true
        continue
      }
      hiddenSection = false
    }
    if (hiddenSection) continue

    if (/本地示例数据|seed:local|本地估算|费用按常见消费水平估算|置信度/.test(line)) {
      removedUnverifiedText = true
      continue
    }
    kept.push(line)
  }

  if (!removedUnverifiedText) return text
  while (kept.length > 0 && kept[kept.length - 1].trim() === '') kept.pop()
  kept.push(
    '',
    '## 数据说明',
    '',
    '- **费用：暂无数据**',
    '- **门票：暂无数据**',
    '- **路线：暂无数据**',
    '- **坐标：暂无数据**',
  )
  return kept.join('\n')
}

const PLAN_STATE_KEY = 'travel:plan-state'

/** 刷新同一标签页时保留当前行程与未应用草案，存储失败则退化为空状态。 */
export function persistPlanState(state: PlanState): void {
  if (typeof window === 'undefined') return
  try {
    window.sessionStorage.setItem(PLAN_STATE_KEY, JSON.stringify(state))
  } catch {
    // 隐私模式或存储空间不足时不阻断规划主流程。
  }
}

export function readPlanState(): PlanState {
  if (typeof window === 'undefined') return EMPTY_PLAN_STATE
  try {
    const raw = window.sessionStorage.getItem(PLAN_STATE_KEY)
    if (!raw) return EMPTY_PLAN_STATE
    const parsed = JSON.parse(raw) as Partial<PlanState>
    if (!parsed || typeof parsed !== 'object') return EMPTY_PLAN_STATE
    return {
      plan: parsed.plan ?? null,
      pending: parsed.pending ?? null,
      notice: typeof parsed.notice === 'string' ? parsed.notice : '',
    }
  } catch {
    return EMPTY_PLAN_STATE
  }
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

/**
 * 收养已有线程（= 恢复一份历史规划）：当前会话切到既有 conversation，
 * 只换持久化指向、不产生新 id。后续「助手改单」会继续在该线程上出新版本，
 * 与后端版本账本/checkpointer 的会话归属保持一致。
 */
export function adoptConversationId(id: string): string {
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
