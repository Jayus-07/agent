'use client'

/**
 * TravelChatDrawer — /travel 右侧「旅行助手」（2026-10-01 三栏方案版）
 *
 * 双形态（mode）：宽屏（xl+）作为**常驻右栏**嵌进页面 grid（panel）；
 * 中窄屏退回**覆盖抽屉**（drawer，右缘按钮唤起、Esc 收起、不挡主体——
 * 改完要能立刻看到左侧行程更新）。两种形态共用同一套对话逻辑与样式。
 *
 * 视觉基准 = 参考 HTML（旅游规划页面交互示意）：用户气泡软底、助手气泡
 * 白底描边、输入区圆角、主色青绿 #087b73。顶部标注「正在查看 vN」——
 * 用户永远知道对话针对的是哪一版行程（方案 v2 §2.3）。
 *
 * 走的是与表单同一个端点 /api/travel/plan/stream（见 src/api/travel.ts），
 * 关键差别只有一个：**复用同一个 conversation_id**。后端 PostgresSaver
 * checkpointer 按 thread_id 取回上一轮 brief 合并 + 指纹比对，需求变了才重排。
 * 因此：
 *   - 助手里说的话 → 后端合并进同一份行程 → 页面上的行程被替换（onResponse）
 *   - 后端只回追问（itinerary=null）→ 由 planState.applyPlanResponse 保留旧行程
 *     （一次追问不会把用户已经看到的行程清掉）
 *
 * 当前旅游域端点返回真实 stage/tool 事件与完整 itinerary JSON；过程面板只
 * 投影服务端事件，不把没有发生的阶段或 Tool 伪装成实时进度。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertCircle, Check, CheckCircle2, Clock3, Info, MapPin, MessageSquarePlus,
  PlaneTakeoff, RefreshCw, RotateCcw, Send, Square, X,
} from 'lucide-react'
import {
  confirmTravelPlan, type Itinerary, type ItineraryBrief, type PlanResponse,
  streamTravelPlan, type TravelStreamEvent,
} from '@/api/travel'
import BudgetRing from '@/components/chat/BudgetRing'
import type { BudgetStatus } from '@/api/budgets'
import MarkdownContent from '@/components/chat/MarkdownContent'
import { describePlanReply, sanitizeTravelReply } from './planState'
import { TRAVEL_STAGE_LABELS, TRAVEL_TOOL_LABELS } from './travelDisplay'
import {
  buildChangeSummary, travelProcessStatusLabel, type TravelProcessState,
} from './travelRuntime'

interface ChatMsg {
  role: 'user' | 'assistant'
  text: string
  tag?: string
  tone?: 'ok' | 'warn'
}

interface Props {
  /** drawer = 覆盖抽屉（中窄屏，右缘按钮唤起）；panel = 常驻右栏（宽屏 grid 列） */
  mode: 'drawer' | 'panel'
  /** 仅 drawer 形态使用：收起/展开状态 */
  open?: boolean
  onOpen?: () => void
  onClose?: () => void
  /** 当前行程版本号（头部「正在查看 vN」） */
  planVersion?: number
  /** 用户在行程视图选中的天（null = 没有行程/未选）：头部徽章 + 快捷话术联动 */
  activeDay?: number | null
  /** 当前行程的会话线程（= 后端 checkpoint 的 thread_id） */
  conversationId: string
  /** 当前结构化 TripBrief，用于在对话区展示已被记住的槽位 */
  brief?: ItineraryBrief | null
  /** 当前行程快照，修改预览用它计算确定性差异 */
  itinerary?: Itinerary | null
  /** 页面上是否已经有行程 —— 只影响文案，不影响行为 */
  hasItinerary: boolean
  /** 拿到后端回复后交给页面合并（planState.applyPlanResponse） */
  onResponse: (data: PlanResponse) => void
  /** 生成草案后只进入预览态，不替换主内容当前行程。 */
  pendingResponse: PlanResponse | null
  onDraft: (data: PlanResponse) => void
  onDiscardPending: () => void
  /** 表单和右侧助手共享同一份真实事件过程。 */
  processState: TravelProcessState | null
  onProcessEvent: (event: TravelStreamEvent) => void
  /** 开一份新行程（页面轮换 conversation_id → 本组件检测到变化后清空对话） */
  onStartNewTrip: () => void
  /** 预算硬额度已满：禁止再发起规划（与 /agent 输入框同一口径） */
  disabled?: boolean
  disabledHint?: string
  /** 预算圆圈数据（页面 useBudgetStatus 轮询下发，与 /agent 同一组件） */
  budgetStatus?: BudgetStatus | null
}

/**
 * 快捷话术：**逐条对着后端槽位抽取的真实关键词写**（travel/models/brief.py
 * 的 PREFERENCE_KEYWORDS / PACE_KEYWORDS / DIET_KEYWORDS 与 requirement_agent
 * 的天数、预算正则），不是拍脑袋的示例 —— 写歪了用户点一下没反应，
 * 比不给提示还糟。有行程且用户选中了某天时，第一条换成针对当天的说法
 * （与行程视图日卡片联动，话术仍走同一个「对话改行程」通道）。
 */
const BASE_QUICK_PROMPTS = [
  '改成 3 天',
  '节奏轻松点',
  '预算 2000 元',
  '喜欢自然、美食',
  '不吃辣',
] as const

function quickPrompts(activeDay: number | null | undefined, hasItinerary: boolean): string[] {
  if (hasItinerary && activeDay != null) {
    return [`第 ${activeDay} 天别排太满`, ...BASE_QUICK_PROMPTS.slice(0, 4)]
  }
  return [...BASE_QUICK_PROMPTS]
}

export default function TravelChatDrawer({
  mode, open = true, onOpen, onClose, planVersion, activeDay = null, conversationId, hasItinerary,
  brief = null, itinerary = null, onResponse, processState, onProcessEvent,
  onStartNewTrip, pendingResponse, onDraft, onDiscardPending, disabled, disabledHint, budgetStatus = null,
}: Props) {
  const [messages, setMessages] = useState<ChatMsg[]>([])
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [lastRequest, setLastRequest] = useState('')
  const [applying, setApplying] = useState(false)
  const [stopped, setStopped] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const prevConvRef = useRef(conversationId)
  const currentRunRef = useRef('')

  const isDrawer = mode === 'drawer'

  // 换线程（= 开了新行程）→ 上一次的对话与在途请求都作废
  useEffect(() => {
    if (prevConvRef.current === conversationId) return
    prevConvRef.current = conversationId
    abortRef.current?.abort()
    setMessages([])
    setError('')
    setLoading(false)
    setStopped(false)
  }, [conversationId])
  // 自动滚到底（新消息 / 进行中提示出现时）
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, loading, open])

  // Esc 收起（仅抽屉形态；面板是常驻栏没有「收起」语义）
  useEffect(() => {
    if (!isDrawer || !open || !onClose) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [isDrawer, open, onClose])

  // 卸载时中断在途请求（避免对已卸载组件 setState）
  useEffect(() => () => abortRef.current?.abort(), [])

  const send = useCallback(async (raw: string) => {
    const message = raw.trim()
    if (!message || loading || disabled) return
    // 记下发起时所属的线程：请求返回时若线程已换（用户点了「新行程」），
    // 这条回复属于旧行程，不能再往新对话里写。
    const sentConv = conversationId
    const clientRunId = `client-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    currentRunRef.current = clientRunId
    setStopped(false)
    setLastRequest(message)
    setError('')
    setText('')
    setMessages((prev) => [...prev, { role: 'user', text: message }])
    setLoading(true)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      let data: PlanResponse | null = null
      for await (const event of streamTravelPlan(message, conversationId, { signal: controller.signal })) {
        onProcessEvent(event)
        if (event.event === 'error') {
          throw new Error(typeof event.data.message === 'string'
            ? event.data.message : '旅游规划执行失败')
        }
        if (event.event === 'done' && event.data.result && typeof event.data.result === 'object') {
          data = event.data.result as PlanResponse
        }
      }
      if (prevConvRef.current !== sentConv || currentRunRef.current !== clientRunId) return
      if (!data) throw new Error('旅游规划流未返回结构化结果')
      if (data.status === 'failed') {
        throw new Error(data.final_answer || '旅游规划执行失败')
      }
      const { tag, tone } = describePlanReply(data)
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          text: sanitizeTravelReply(
            data.itinerary ? (data.final_answer || '已生成一份调整预览。') : (data.final_answer || '（没有返回内容）'),
          ),
          tag: data.itinerary ? `预览中 · v${data.itinerary.plan_version}` : tag,
          tone: data.itinerary ? 'ok' : tone,
        },
      ])
      if (data.itinerary) {
        onDraft(data)
      } else {
        onResponse(data)
      }
    } catch (e) {
      if (prevConvRef.current !== sentConv || currentRunRef.current !== clientRunId) return
      if (controller.signal.aborted) {
        // 用户主动中止：留一行痕迹，不当成错误
        setMessages((prev) => [...prev, {
          role: 'assistant', text: '（已停止本次规划，已完成的结果仍保留）', tag: '已停止', tone: 'warn',
        }])
      } else {
        setError(e instanceof Error ? e.message : '调整失败，请稍后再试')
        // 输入框在请求期间是禁用的，用户不可能在改，原样还回去让他重发
        setText(message)
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      if (prevConvRef.current === sentConv) setLoading(false)
    }
  }, [conversationId, disabled, hasItinerary, loading, onDraft, onProcessEvent, onResponse])

  const stop = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setStopped(true)
    setLoading(false)
  }, [])

  const applyPending = useCallback(async () => {
    if (!pendingResponse?.itinerary || applying) return
    setApplying(true)
    setError('')
    try {
      if (pendingResponse.plan_status === 'waiting_confirmation') {
        await confirmTravelPlan(conversationId, pendingResponse.itinerary.plan_version)
      }
      onResponse(pendingResponse.plan_status === 'waiting_confirmation'
        ? { ...pendingResponse, plan_status: 'confirmed' }
        : pendingResponse)
      setMessages((prev) => [...prev, {
        role: 'assistant',
        text: `已将 v${pendingResponse.itinerary?.plan_version} 应用到当前行程。`,
        tag: '已应用',
        tone: 'ok',
      }])
    } catch (e) {
      setError(e instanceof Error ? e.message : '确认行程失败，请刷新后重试')
    } finally {
      setApplying(false)
    }
  }, [applying, conversationId, onResponse, pendingResponse])

  const discardPending = useCallback(() => {
    if (!pendingResponse) return
    setMessages((prev) => [...prev, {
      role: 'assistant', text: '已保留原行程，这次修改没有应用到当前视图。', tag: '未应用', tone: 'warn',
    }])
    onDiscardPending()
  }, [onDiscardPending, pendingResponse])

  const retry = useCallback(() => {
    if (lastRequest && !loading) void send(lastRequest)
  }, [lastRequest, loading, send])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void send(text)
    }
  }

  const hasMessages = messages.length > 0
  const inputDisabled = disabled || loading || Boolean(pendingResponse)
  const pendingSummary = useMemo(
    () => pendingResponse?.itinerary && itinerary
      ? buildChangeSummary(itinerary, pendingResponse.itinerary)
      : null,
    [itinerary, pendingResponse],
  )
  const briefItems = useMemo(() => {
    if (!brief) return []
    return [
      brief.destination && `目的地 ${brief.destination}`,
      brief.origin && `出发地 ${brief.origin}`,
      brief.days ? `${brief.days} 天` : '',
      brief.start_date ? `${brief.start_date} 出发` : '',
      brief.party_size ? `${brief.party_size} 人` : '',
      brief.budget_cny != null ? `预算 ¥${brief.budget_cny}` : '',
      brief.pace ? `节奏 ${brief.pace === 'relaxed' ? '轻松' : brief.pace === 'intense' ? '紧凑' : '适中'}` : '',
      ...((brief.preferences ?? []).map((item) => `偏好 ${item}`)),
    ].filter(Boolean) as string[]
  }, [brief])

  const body = (
    <>
      {/* Header */}
      <div className="flex shrink-0 items-center gap-2.5 border-b border-[#dae7e5] px-4 py-3">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[#087b73]/10">
          <PlaneTakeoff size={17} className="text-[#087b73]" />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-sm font-semibold text-[#183037]">
            旅行助手
            {planVersion != null && (
              <span className="ml-1.5 rounded-full border border-[#dae7e5] bg-[#f5faf9] px-1.5 py-0.5 text-[10px] font-normal text-[#5c7074]">
                正在查看 v{planVersion}
              </span>
            )}
            {hasItinerary && activeDay != null && (
              <span className="ml-1 rounded-full bg-[#087b73]/10 px-1.5 py-0.5 text-[10px] font-normal text-[#087b73]">
                第 {activeDay} 天
              </span>
            )}
          </h2>
          <p className="truncate text-[10px] text-[#5c7074]">
            {hasItinerary
              ? activeDay != null
                ? `修改会先出预览；提「第 ${activeDay} 天」会针对当天调整`
                : '先预览，应用后更新当前行程'
              : '直接说需求，我会出一份行程'}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={onStartNewTrip}
            title="开一份新行程（清空当前对话，不沿用旧约束）"
            aria-label="开一份新行程"
            className="flex h-7 items-center gap-1 rounded-lg border border-[#dae7e5] px-2
              text-[11px] text-[#5c7074] transition-colors
              hover:border-[#087b73]/40 hover:text-[#183037]"
          >
            <RotateCcw size={12} />
            新行程
          </button>
          {isDrawer && (
            <button
              type="button"
              onClick={onClose}
              title="收起对话框"
              aria-label="收起对话框"
              className="flex h-7 w-7 items-center justify-center rounded-lg text-[#5c7074]
                transition-colors hover:bg-[#f5faf9] hover:text-[#183037]"
            >
              <X size={16} />
            </button>
          )}
        </div>
      </div>

      {/* 预算阻断提示（与 /agent 输入框同一口径） */}
      {disabled && (
        <div className="mx-3 mt-2 flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <Info size={13} className="shrink-0" />
          <span className="min-w-0 flex-1">{disabledHint ?? '当前额度已用尽，暂时不能发起新的规划'}</span>
        </div>
      )}

      {briefItems.length > 0 && (
        <div className="mx-3 mt-2 rounded-xl border border-[#dae7e5] bg-[#f5faf9] px-3 py-2.5">
          <div className="flex items-center gap-1.5 text-[11px] font-semibold text-[#183037]">
            <MapPin size={12} className="text-[#087b73]" aria-hidden />
            已记住的行程条件
          </div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {briefItems.map((item) => (
              <span key={item} className="rounded-full bg-white px-2 py-1 text-[10px] text-[#5c7074]">
                {item}
              </span>
            ))}
          </div>
        </div>
      )}

      <details className="mx-3 mt-2 rounded-xl border border-[#dae7e5] bg-white" open={loading || stopped || processState?.status === 'error'}>
        <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2.5 text-[11px] font-semibold text-[#183037] [&::-webkit-details-marker]:hidden">
          <span className={`h-2 w-2 rounded-full ${loading ? 'animate-pulse bg-[#087b73]' : processState?.status === 'error' ? 'bg-red-400' : stopped ? 'bg-amber-400' : processState ? 'bg-[#087b73]' : 'bg-[#9fc1ba]'}`} aria-hidden />
          本轮处理过程
          {(processState?.tools.length ?? 0) > 0 && (
            <span className="rounded-full bg-[#f5faf9] px-1.5 py-0.5 text-[10px] font-normal text-[#087b73]">
              已调用 {processState?.tools.length} 个 Tool
            </span>
          )}
          <span className="ml-auto text-[10px] font-normal text-[#5c7074]">
            {travelProcessStatusLabel({ loading, stopped, status: processState?.status })}
          </span>
        </summary>
        <div className="border-t border-[#dae7e5] px-3 py-2">
          <p className="mb-2 text-[10px] leading-relaxed text-[#5c7074]">
            只展示旅游域实际发出的阶段和 Tool 事件；失败、空结果和降级不会被改写成成功。
          </p>
          {processState?.requirement && (
            <div className="mb-3 rounded-lg border border-[#b8d8d0] bg-[#f5faf9] px-2.5 py-2">
              <p className="text-[10px] font-semibold text-[#183037]">需求理解已完成</p>
              <p className="mt-1 text-[10px] leading-relaxed text-[#5c7074]">
                {String(processState.requirement.brief.destination || '目的地待确认')}
                {processState.requirement.brief.days ? ` · ${String(processState.requirement.brief.days)} 天` : ''}
                {processState.requirement.assumptions.length > 0 ? ` · ${processState.requirement.assumptions[0]}` : ''}
              </p>
            </div>
          )}
          {(processState?.stages && Object.keys(processState.stages).length > 0) && (
            <ol className="space-y-2">
              {Object.entries(processState.stages).map(([stageKey, stage]) => (
                <li key={stageKey} className="flex items-start gap-2">
                <span className="mt-0.5 shrink-0">
                  {stage.status === 'completed' ? <CheckCircle2 size={13} className="text-[#087b73]" aria-label="已完成" />
                    : stage.status === 'running' ? <Clock3 size={13} className="animate-pulse text-[#087b73]" aria-label="进行中" />
                      : <AlertCircle size={13} className="text-red-500" aria-label="执行失败" />}
                </span>
                <span className="min-w-0">
                  <span className="block text-[11px] text-[#183037]">{TRAVEL_STAGE_LABELS[stageKey] ?? stageKey}</span>
                  <span className="block text-[10px] leading-relaxed text-[#7a8e8b]">{stage.status === 'running' ? '正在执行' : stage.status === 'completed' ? '已完成' : '执行失败'}</span>
                </span>
              </li>
              ))}
            </ol>
          )}
          {(processState?.tools.length ?? 0) > 0 && (
            <div className="mt-3 border-t border-[#eef4f2] pt-2">
              <p className="mb-1.5 text-[10px] font-semibold text-[#5c7074]">实际 Tool 调用</p>
              <ul className="space-y-1.5">
                {processState?.tools.map((tool, index) => (
                  <li key={`${tool.tool}-${index}`} className="flex items-center gap-2 text-[10px]">
                    {tool.status === 'running' ? <Clock3 size={12} className="animate-pulse text-[#087b73]" aria-label="进行中" />
                      : tool.status === 'success' ? <CheckCircle2 size={12} className="text-[#087b73]" aria-label="成功" />
                        : <AlertCircle size={12} className="text-red-500" aria-label="失败" />}
                    <span className="min-w-0 flex-1 truncate text-[#183037]">{TRAVEL_TOOL_LABELS[tool.tool] ?? tool.tool}</span>
                    <span className={tool.status === 'failed' ? 'text-red-500' : 'text-[#7a8e8b]'}>
                      {tool.status === 'running' ? '调用中' : tool.status === 'failed' ? `失败${tool.errorType ? ` · ${tool.errorType}` : ''}` : tool.dataStatus === 'empty' ? '空结果' : tool.dataStatus === 'unavailable' ? '不可用' : '已返回'}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </details>

      {/* 内容 */}
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {hasMessages ? (
          <ul className="space-y-3" aria-live="polite" aria-label="旅行助手消息">
            {messages.map((m, i) => {
              // 长回复折叠：改单回复常是整份行程单 Markdown，全量铺开字多压迫感强
              //（用户实测反馈「字很大不友好」）；tag 摘要常驻，全文点开再看
              const isLong = m.role === 'assistant' && m.text.length > 600
              return (
                <li key={i} className={m.role === 'user' ? 'flex justify-end' : 'flex justify-start'}>
                  <div className={`max-w-[94%] rounded-2xl px-3 py-2 text-xs leading-relaxed ${
                    m.role === 'user'
                      ? 'border border-[#d5e5e0] bg-[#e9f3f0] text-[#183037]'
                      : 'border border-[#dae7e5] bg-white text-[#183037]'
                  }`}>
                    {m.role === 'user' ? (
                      <p className="whitespace-pre-wrap break-words">{m.text}</p>
                    ) : isLong ? (
                      <details>
                        <summary className="cursor-pointer text-[11px] font-medium text-[#087b73] [&::-webkit-details-marker]:hidden">
                          行程已按你说的重算，点开查看完整说明 ▾
                        </summary>
                        <div className="mt-1.5 markdown-body text-xs leading-relaxed
                          [&_h1]:text-sm [&_h1]:my-1.5 [&_h2]:text-[13px] [&_h2]:my-1.5 [&_h3]:text-xs [&_h3]:my-1
                          [&_p]:my-1 [&_li]:my-0.5 [&_hr]:my-2">
                          <MarkdownContent content={m.text} />
                        </div>
                      </details>
                    ) : (
                      <div className="markdown-body text-xs leading-relaxed
                        [&_h1]:text-sm [&_h1]:my-1.5 [&_h2]:text-[13px] [&_h2]:my-1.5 [&_h3]:text-xs [&_h3]:my-1
                        [&_p]:my-1 [&_li]:my-0.5 [&_hr]:my-2">
                        <MarkdownContent content={m.text} />
                      </div>
                    )}
                    {m.tag && (
                      <span className={`mt-2 inline-flex items-center rounded-full px-2 py-0.5 text-[10px] ${
                        m.tone === 'warn' ? 'bg-amber-100 text-amber-800' : 'bg-[#087b73]/10 text-[#087b73]'
                      }`}>
                        {m.tag}
                      </span>
                    )}
                  </div>
                </li>
              )
            })}
          </ul>
        ) : (
          <div className="space-y-4">
            <div className="rounded-xl border border-dashed border-[#c9dcd7] bg-[#f5faf9] px-4 py-5">
              <p className="text-sm font-medium text-[#183037]">
                {hasItinerary ? '想怎么改？直接说一句就行' : '先说说想去哪、几天、几个人'}
              </p>
              <p className="mt-1 text-xs leading-relaxed text-[#5c7074]">
                改动会先生成预览，应用后才更新当前行程。说得越具体越准（比如「第二天别排太满」
                「把预算压到 2000」）。同一标签页刷新后还能接着改。
              </p>
            </div>
            <div>
              <p className="mb-2 text-[11px] text-[#5c7074]">常用说法</p>
              <div className="flex flex-wrap gap-2">
                {quickPrompts(activeDay, hasItinerary).map((p) => (
                  <button
                    key={p}
                    type="button"
                    disabled={inputDisabled}
                    onClick={() => void send(p)}
                    className="rounded-full border border-[#dae7e5] bg-white px-3 py-1.5 text-xs
                      text-[#5c7074] transition-colors hover:border-[#087b73]/40 hover:text-[#087b73]
                      disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {p}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {pendingResponse?.itinerary && (
          <section className="mt-3 rounded-xl border border-[#a9d1c9] bg-[#f5faf9] p-3" aria-label="行程修改预览">
            <div className="flex items-start gap-2">
              <CheckCircle2 size={15} className="mt-0.5 shrink-0 text-[#087b73]" aria-hidden />
              <div className="min-w-0 flex-1">
                <h3 className="text-xs font-semibold text-[#183037]">
                  调整预览 · v{pendingResponse.itinerary.plan_version}
                </h3>
                <p className="mt-1 text-[10px] leading-relaxed text-[#5c7074]">
                  页面顶部已标记「预览中，尚未应用」；当前行程仍保持不变。
                </p>
                {pendingSummary ? (
                  <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[10px] text-[#5c7074]">
                    <div><dt className="inline text-[#8aa09c]">版本：</dt><dd className="inline">v{pendingSummary.fromVersion} → v{pendingSummary.toVersion}</dd></div>
                    <div><dt className="inline text-[#8aa09c]">费用：</dt><dd className="inline text-red-600">暂无核验数据</dd></div>
                    <div className="col-span-2"><dt className="inline text-[#8aa09c]">槽位：</dt><dd className="inline">{pendingSummary.briefFields.length ? pendingSummary.briefFields.join('、') : '未改变'}</dd></div>
                    {pendingSummary.moved.length > 0 && (
                      <div className="col-span-2"><dt className="inline text-[#8aa09c]">移动：</dt><dd className="inline">{pendingSummary.moved.map((item) => `${item.name} 第${item.fromDay}天→第${item.toDay}天`).join('、')}</dd></div>
                    )}
                  </dl>
                ) : (
                  <p className="mt-2 text-[10px] text-[#5c7074]">这是当前会话的首份行程，确认后会作为当前版本。</p>
                )}
                <div className="mt-3 flex gap-2">
                  <button
                    type="button"
                    onClick={() => void applyPending()}
                    disabled={applying}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-[#087b73] px-3 py-1.5 text-[11px] font-medium text-white hover:bg-[#06655f] disabled:opacity-50"
                  >
                    {applying ? <Clock3 size={12} className="animate-pulse" /> : <Check size={12} />}
                    {applying ? '确认中…' : '应用新行程'}
                  </button>
                  <button
                    type="button"
                    onClick={discardPending}
                    disabled={applying}
                    className="rounded-lg border border-[#c9dcd7] bg-white px-3 py-1.5 text-[11px] text-[#5c7074] hover:text-[#183037] disabled:opacity-50"
                  >
                    保留原行程
                  </button>
                </div>
              </div>
            </div>
          </section>
        )}

        {loading && (
          <div className="mt-3 flex items-center gap-2 text-xs text-[#5c7074]">
            <span className="flex gap-0.5" aria-hidden>
              <i className="h-1 w-1 animate-bounce rounded-full bg-[#087b73]" />
              <i className="h-1 w-1 animate-bounce rounded-full bg-[#087b73] [animation-delay:120ms]" />
              <i className="h-1 w-1 animate-bounce rounded-full bg-[#087b73] [animation-delay:240ms]" />
            </span>
            正在等待结构化规划结果（不会显示虚构进度）…
          </div>
        )}

        {error && (
          <div className="mt-3 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">
            <span className="min-w-0 flex-1 break-words">{error}</span>
            <button type="button" onClick={() => setError('')} className="shrink-0 text-red-400 hover:text-red-600">
              关闭
            </button>
          </div>
        )}

        {!loading && processState?.status === 'error' && lastRequest && (
          <button
            type="button"
            onClick={retry}
            className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-[#c9dcd7] bg-white px-3 py-1.5 text-xs text-[#087b73] hover:bg-[#f5faf9]"
          >
            <RefreshCw size={12} /> 重新回答
          </button>
        )}
      </div>

      {/* 输入 */}
      <div className="shrink-0 border-t border-[#dae7e5] px-4 py-3">
        <div className="flex items-end gap-2">
          {/* 预算圆圈：与 /agent 输入框同组件同位置（左簇第一位） */}
          <div className="pb-2">
            <BudgetRing status={budgetStatus} />
          </div>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={inputDisabled}
            rows={1}
            placeholder={pendingResponse ? '请先应用或保留当前预览，再继续修改…' : hasItinerary ? '比如：第二天别排太满…' : '比如：杭州 3 天，2 个人，喜欢自然…'}
            aria-label="改行程的对话输入"
            className="max-h-32 min-w-0 flex-1 resize-none rounded-xl border border-[#dae7e5]
              bg-[#f5faf9] px-4 py-2.5 text-sm text-[#183037] placeholder:text-[#8fa5a3]
              outline-none transition-colors focus:border-[#087b73]/60 focus:bg-white
              disabled:cursor-not-allowed disabled:opacity-60"
          />
          {loading ? (
            <button
              type="button"
              onClick={stop}
              title="停止这次调整"
              aria-label="停止这次调整"
              className="shrink-0 rounded-xl p-2.5 text-red-500 transition-colors hover:bg-red-50"
            >
              <Square size={15} fill="currentColor" />
            </button>
          ) : (
            <button
              type="button"
              onClick={() => void send(text)}
              disabled={!text.trim() || disabled}
              title="发送"
              aria-label="发送"
              className="shrink-0 rounded-xl bg-[#087b73] p-2.5 text-white transition-colors
                hover:bg-[#06655f] disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Send size={15} />
            </button>
          )}
        </div>
      </div>
    </>
  )

  if (isDrawer) {
    return (
      <>
        {/* 右侧边缘按钮：收起态常驻，点开变成对话框 */}
        {!open && onOpen && (
          <button
            type="button"
            onClick={onOpen}
            className="fixed right-0 top-1/2 z-30 flex -translate-y-1/2 items-center gap-2 rounded-l-xl
              border border-r-0 border-[#dae7e5] bg-white py-3 pl-3 pr-2.5
              text-xs text-[#5c7074] shadow-card transition-colors
              hover:border-[#087b73]/40 hover:text-[#087b73]"
            title="和 AI 对话，改当前行程"
            aria-label="展开对话改行程"
          >
            <MessageSquarePlus size={15} />
            <span className="[writing-mode:vertical-rl] tracking-[0.2em]">对话改行程</span>
          </button>
        )}

        {/* 抽屉面板（不盖遮罩：改完要能立刻看到左侧行程更新）。
            面板常挂载（与 components/cs/CSDrawer.tsx 同款）：收起靠 translate-x-full
            滑出，动画不断、对话记录也不随开关丢失。
            已知取舍：收起态的面板控件仍可被 Tab 聚焦（与 CSDrawer 同一处欠账，
            若要根治需给整块加 inert/条件挂载，会牺牲滑出动画，留作统一整改）。 */}
        <div
          className={`fixed right-0 top-0 z-50 flex h-full w-[440px] max-sm:w-full flex-col
            border-l border-[#dae7e5] bg-white shadow-2xl
            transition-transform duration-300 ease-out
            ${open ? 'translate-x-0' : 'translate-x-full'}`}
        >
          {body}
        </div>
      </>
    )
  }

  // panel 形态：常驻右栏（页面 grid 列内，父级给高度）
  return (
    <section
      aria-label="旅行助手"
      className="flex h-full min-h-0 w-full flex-col overflow-hidden rounded-2xl border border-[#dae7e5] bg-white shadow-card"
    >
      {body}
    </section>
  )
}
