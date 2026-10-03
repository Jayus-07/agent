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
import Link from 'next/link'
import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react'
import {
  AlertCircle, Check, CheckCircle2, Clock3, Info, MessageSquarePlus,
  PlaneTakeoff, RefreshCw, Send, Square, X,
} from 'lucide-react'
import {
  confirmTravelPlan, type Itinerary, type ItineraryBrief, type PlanResponse,
  streamTravelPlan, type TravelStreamEvent,
  type TravelClarificationOption,
} from '@/api/travel'
import BudgetRing from '@/components/chat/BudgetRing'
import type { BudgetStatus } from '@/api/budgets'
import MarkdownContent from '@/components/chat/MarkdownContent'
import ToolProcessRows from './ToolProcessRows'
import RationaleCard, { type RationaleData } from './RationaleCard'
import { useTypewriter } from './useTypewriter'
import { describePlanReply, sanitizeTravelReply } from './planState'
import {
  buildChangeSummary, travelProcessStatusLabel, type TravelProcessState,
} from './travelRuntime'

interface ChatMsg {
  role: 'user' | 'assistant'
  text: string
  tag?: string
  tone?: 'ok' | 'warn'
  /** M2：结构化「为什么这样排」（后端 PlanResponse.rationale），有值时渲染 RationaleCard */
  rationale?: RationaleData
}

/**
 * 短助手气泡 + 打字机渐显（M2-e）：仅对最新一条短回复开动画，
 * 历史/长文（details 折叠）直显；reduced-motion 由 hook 内部兜底。
 */
function TypedAssistantText({ text, animate }: { text: string; animate: boolean }) {
  const shown = useTypewriter(text, animate)
  return (
    <div className="markdown-body travel-md leading-relaxed">
      <MarkdownContent content={shown} />
    </div>
  )
}

export interface TravelChatDrawerHandle {
  /** M2 代发：画布直选/结果卡按钮把修改请求送进同一聊天管线（忙时进排队槽） */
  send: (text: string) => void
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
  /** 页面级生成中（设计稿态2）：输入禁用 + 内容区占位，防止半成品行程被对话改乱 */
  generating?: boolean
  /** 预算硬额度已满：禁止再发起规划（与 /agent 输入框同一口径） */
  disabled?: boolean
  disabledHint?: string
  /** 预算圆圈数据（页面 useBudgetStatus 轮询下发，与 /agent 同一组件） */
  budgetStatus?: BudgetStatus | null
  /** 出单后的首条助手消息（2026-10-03）：reporter 的规划说明全文
   *（为什么这样排/美食推荐/需要你确认），表单直出路径此前完全不展示 */
  introMessage?: string
  /** 首轮规划的结构化说明（页面 planState.plan.rationale 传入） */
  introRationale?: RationaleData | null
  /** M3-h 聊天 chip 唤起城市指南抽屉 */
  onOpenCityGuide?: () => void
  /** M1 三态移交：空态规划卡发送的那句话，作为第一条用户气泡出现在聊天流 */
  handoverUserMessage?: string | null
}

/**
 * 快捷话术已随设计稿③精简移除（对话引导收进空态一行提示与输入框 placeholder）。
 */

const TravelChatDrawerImpl = forwardRef<TravelChatDrawerHandle, Props>(function TravelChatDrawer({
  mode, open = true, onOpen, onClose, planVersion, activeDay = null, conversationId, hasItinerary,
  brief = null, itinerary = null, onResponse, processState, onProcessEvent,
  onStartNewTrip, pendingResponse, onDraft, onDiscardPending, generating = false, disabled, disabledHint, budgetStatus = null,
  introMessage = '', introRationale = null, handoverUserMessage = null, onOpenCityGuide,
}: Props, ref) {
  const [messages, setMessages] = useState<ChatMsg[]>([])
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [lastRequest, setLastRequest] = useState('')
  const [applying, setApplying] = useState(false)
  const [stopped, setStopped] = useState(false)
  const [clarificationOptions, setClarificationOptions] = useState<TravelClarificationOption[]>([])
  const [fillingDays, setFillingDays] = useState(false)
  // M2-f 生成中排队的下一条消息（单条槽）
  const [queuedText, setQueuedText] = useState('')
  // M2-a 本轮完成后执行明细默认收敛，点开看全量
  const [processExpanded, setProcessExpanded] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)
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
    currentRunRef.current = ''
    setMessages([])
    setError('')
    setLoading(false)
    setStopped(false)
    setClarificationOptions([])
    setFillingDays(false)
    setQueuedText('')
  }, [conversationId])
  // M1 三态移交：空态规划卡/示例卡发出的话 → 聊天流第一条用户气泡。
  // 只在「本线程还没有这条消息」时追加一次，避免面板/抽屉双挂载或重渲染重复。
  useEffect(() => {
    if (!handoverUserMessage) return
    setMessages((prev) => {
      if (prev.some((m) => m.role === 'user' && m.text === handoverUserMessage)) return prev
      return [...prev, { role: 'user', text: handoverUserMessage }]
    })
  }, [handoverUserMessage])

  // 自动滚到底（新消息 / 进行中提示出现时）
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, loading, open, processState])

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
    if (!message) return
    // M2-f 排队槽：本轮忙时消息不丢，进单条排队槽（可编辑/可取消，空闲自动发）
    if (abortRef.current || loading || generating || pendingResponse) {
      setQueuedText(message)
      setText('')
      return
    }
    if (disabled) return
    // 记下发起时所属的线程：请求返回时若线程已换（用户点了「新行程」），
    // 这条回复属于旧行程，不能再往新对话里写。
    const sentConv = conversationId
    const clientRunId = `client-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    currentRunRef.current = clientRunId
    setStopped(false)
    setLastRequest(message)
    setError('')
    setText('')
    setClarificationOptions([])
    setFillingDays(false)
    setMessages((prev) => [...prev, { role: 'user', text: message }])
    setLoading(true)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      let data: PlanResponse | null = null
      for await (const event of streamTravelPlan(message, conversationId, { signal: controller.signal })) {
        if (controller.signal.aborted || currentRunRef.current !== clientRunId) break
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
      setClarificationOptions(data.clarification_options ?? [])
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
          rationale: (data as { rationale?: RationaleData }).rationale || undefined,
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
      if (prevConvRef.current === sentConv && currentRunRef.current === clientRunId) setLoading(false)
    }
  }, [conversationId, disabled, generating, hasItinerary, loading, onDraft, onProcessEvent, onResponse, pendingResponse])

  // M2-c 代发：画布直选/结果卡按钮经 ref 走同一 send 管线
  useImperativeHandle(ref, () => ({
    send: (text: string) => { void send(text) },
  }), [send])

  // M2-f 排队槽自动发送：本轮结束（且无草案待决、未禁用）即发出
  useEffect(() => {
    if (loading || generating || disabled || pendingResponse || !queuedText) return
    if (abortRef.current) return
    const t = queuedText
    setQueuedText('')
    void send(t)
  }, [loading, generating, disabled, pendingResponse, queuedText, send])

  const stop = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    currentRunRef.current = ''
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

  // 出单规划说明（「为什么这样排/美食推荐」）作为首条助手消息常驻对话流。
  // 时序：移交的用户气泡（空态发送的那句话）排在规划说明**之前**——
  // 用户先提问、后出说明；说明之后再来的对话按原顺序追加。
  const visibleMessages = useMemo<ChatMsg[]>(() => {
    if (!introMessage.trim()) return messages
    const intro: ChatMsg = {
      role: 'assistant', text: introMessage.trim(), tag: '规划说明',
      rationale: introRationale ?? undefined,
    }
    const firstAssistant = messages.findIndex((m) => m.role === 'assistant')
    const cut = firstAssistant === -1 ? messages.length : firstAssistant
    return [...messages.slice(0, cut), intro, ...messages.slice(cut)]
  }, [introMessage, introRationale, messages])
  // M2-h 快捷话术（2026-10-03 拍板四条；目的地/天数来自真实 brief/activeDay）
  const quickChips = useMemo(() => {
    const dest = (brief?.destination || '').trim()
    const chips: Array<{ key: string; label: string; text: string; cityGuide?: boolean }> = []
    if (dest) chips.push({ key: 'guide', label: `介绍下${dest}特色`, cityGuide: true, text: `介绍下${dest}特色，适合玩几天、有什么必吃必逛` })
    if (hasItinerary && activeDay != null) chips.push({ key: 'crowded', label: `第 ${activeDay} 天太挤了`, text: `第 ${activeDay} 天太挤了，帮我排松一点` })
    chips.push({ key: 'hotel', label: '想住得离海近一点', text: '想住得离海近一点，帮我调整住宿' })
    chips.push({ key: 'budget', label: '预算调到 ¥2000', text: '预算调到 2000，帮我重排行程' })
    return chips
  }, [brief?.destination, hasItinerary, activeDay])

  const hasMessages = visibleMessages.length > 0
  const inputDisabled = disabled || loading || generating || Boolean(pendingResponse)
  const pendingSummary = useMemo(
    () => pendingResponse?.itinerary && itinerary
      ? buildChangeSummary(itinerary, pendingResponse.itinerary)
      : null,
    [itinerary, pendingResponse],
  )
  const pendingChangeCount = pendingSummary
    ? pendingSummary.briefFields.length + pendingSummary.moved.length
    : 0

  // 消息渲染：提问段/回答段两段共用（工具执行块插在两段之间=提问→工具→回答）
  const renderMsg = (m: ChatMsg, i: number, animate: boolean) => {              // 长回复折叠：改单回复常是整份行程单 Markdown，全量铺开字多压迫感强
              //（用户实测反馈「字很大不友好」）；tag 摘要常驻，全文点开再看
              const isLong = m.role === 'assistant' && m.text.length > 600
              return (
                <li key={i} className={m.role === 'user' ? 'flex justify-end' : 'flex justify-start'}>
                  <div className={`max-w-[94%] rounded-2xl px-3.5 py-2.5 text-xs leading-relaxed ${
                    m.role === 'user'
                      ? 'border border-[#d5e5e0] bg-[#e9f3f0] text-[#183037]'
                      : 'border border-[#dae7e5] bg-white text-[#183037]'
                  }`}>
                    {m.role === 'assistant' && m.rationale && Object.keys(m.rationale).length > 0 ? (
                      <RationaleCard
                        rationale={m.rationale}
                        processState={processState}
                        itinerary={itinerary}
                        pace={String((processState?.requirement?.brief as Record<string, unknown> | undefined)?.pace ?? '')}
                        onAsk={(t) => void send(t)}
                      />
                    ) : m.role === 'user' ? (
                      <p className="whitespace-pre-wrap break-words">{m.text}</p>
                    ) : isLong ? (
                      <details>
                        <summary className="cursor-pointer text-[11px] font-medium text-[#087b73] [&::-webkit-details-marker]:hidden">
                          {m.tag === '规划说明' ? '为什么这样排？点开查看规划说明 ▾' : '行程已按你说的重算，点开查看完整说明 ▾'}
                        </summary>
                        <div className="mt-1.5 markdown-body travel-md leading-relaxed">
                          <MarkdownContent content={m.text} />
                        </div>
                      </details>
                    ) : (
                      <TypedAssistantText text={m.text} animate={animate} />
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
  }

  const body = (
    <>
      {/* Header（设计稿③）：标题 + 「正在看：第 N 天」胶囊 */}
      <div className="flex shrink-0 items-center gap-2.5 border-b border-[#dae7e5] px-4 py-3">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[#087b73]/10">
          <PlaneTakeoff size={17} className="text-[#087b73]" />
        </div>
        <h2 className="min-w-0 flex-1 truncate text-sm font-semibold text-[#183037]">旅行助手</h2>
        {hasItinerary && activeDay != null && (
          <span className="shrink-0 rounded-full bg-[#087b73]/10 px-2.5 py-1 text-[11px] font-medium text-[#087b73]">
            正在看：第 {activeDay} 天
          </span>
        )}
        {isDrawer && (
          <button
            type="button"
            onClick={onClose}
            title="收起对话框"
            aria-label="收起对话框"
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-[#5c7074]
              transition-colors hover:bg-[#f5faf9] hover:text-[#183037]"
          >
            <X size={16} />
          </button>
        )}
      </div>

      {/* 预算阻断提示（与 /agent 输入框同一口径） */}
      {disabled && (
        <div className="mx-3 mt-2 flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <Info size={13} className="shrink-0" />
          <span className="min-w-0 flex-1">{disabledHint ?? '当前额度已用尽，暂时不能发起新的规划'}</span>
        </div>
      )}


      {/* 内容 */}
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {(() => {
          // 时序：提问（含移交首问）→ 本轮工具执行块 → 助手回答 → 后续对话
          const lastUserIdx = visibleMessages.map((m) => m.role).lastIndexOf('user')
          const before = visibleMessages.slice(0, lastUserIdx + 1)
          const after = visibleMessages.slice(lastUserIdx + 1)
          const showProcess = processState && (processState.tools.length > 0 || processState.requirement)
          const doneTools = processState?.tools ?? []
          const okCount = doneTools.filter((t) => t.status === 'success').length
          const failCount = doneTools.filter((t) => t.status === 'failed').length
          const withResult = doneTools.filter((t) => (t.preview?.length ?? 0) > 0).length
          // M2-a 收敛口径：进行中逐条实时；完成后收敛成一行摘要（点开看明细）
          const collapsed = !loading && processState?.status === 'completed' && !processExpanded
          const outOfScope = processState?.requirement?.intent === 'out_of_scope'
          const processBlock = showProcess ? (
            <div className="mt-3">
              <div className="mb-1.5 flex items-center gap-2 text-[10px] text-[#8fa5a3]">
                <span
                  className={`h-1.5 w-1.5 rounded-full ${loading ? 'animate-pulse bg-[#087b73]' : processState.status === 'error' ? 'bg-red-400' : 'bg-[#087b73]'}`}
                  aria-hidden
                />
                <span>{loading ? '工具执行中' : `本轮共 ${processState.tools.length} 个 Tool`}</span>
                <span>· {travelProcessStatusLabel({ loading, stopped, status: processState.status })}</span>
              </div>
              {collapsed ? (
                <button
                  type="button"
                  onClick={() => setProcessExpanded(true)}
                  aria-expanded={false}
                  className="flex w-full cursor-pointer items-center gap-2 rounded-xl border border-[#e2f0ee] bg-white px-3 py-2 text-left transition-colors hover:bg-[#f5faf9]"
                >
                  <CheckCircle2 size={13} className={`shrink-0 ${failCount > 0 ? 'text-amber-500' : 'text-[#087b73]'}`} aria-hidden />
                  <span className="min-w-0 flex-1 truncate text-xs font-medium text-[#183037]">
                    {doneTools.length === 0
                      ? '本轮无工具调用 · 直接回答'
                      : `本轮执行完成 · ${okCount} 成功${withResult > 0 ? ` · ${withResult} 个有结果` : ''}${failCount > 0 ? ` · ${failCount} 个失败` : ''}`}
                  </span>
                  <span className="shrink-0 text-[10px] text-[#087b73]">展开明细 ▾</span>
                </button>
              ) : (
                <>
                  {processState.requirement && (
                    <p className="mb-1.5 rounded-lg bg-[#f5faf9] px-2.5 py-1.5 text-[10px] leading-relaxed text-[#5c7074]">
                      <span className="font-semibold text-[#183037]">需求理解</span>
                      {' '}· {String(processState.requirement.brief.destination || '目的地待确认')}
                      {processState.requirement.brief.days ? ` · ${String(processState.requirement.brief.days)} 天` : ''}
                      {processState.requirement.assumptions.length > 0 ? ` · ${processState.requirement.assumptions[0]}` : ''}
                    </p>
                  )}
                  <ol className="space-y-1.5">
                    <ToolProcessRows tools={processState.tools} onAsk={(t) => void send(t)} />
                  </ol>
                </>
              )}
              {outOfScope && (
                <div className="mt-2 rounded-xl border border-[#e6d3ab] bg-[#fffaf0] p-3" aria-label="出域引导">
                  <p className="text-xs font-semibold text-[#8c5a10]">这个问题超出了行程规划范围</p>
                  <p className="mt-1 text-[11px] leading-relaxed text-[#8c7258]">
                    我只懂旅游：排行程、改行程、查车票、找美食景点。订单、查数据、写代码这类问题，请找主页的「AI 助手」。
                  </p>
                  <Link
                    href="/agent"
                    className="mt-2 inline-flex items-center gap-1 rounded-lg bg-[#087b73] px-3 py-1.5 text-[11px] font-medium text-white transition-colors hover:bg-[#06655f]"
                  >
                    去问 AI 助手
                  </Link>
                </div>
              )}
            </div>
          ) : null
          if (!hasMessages) {
            return (
              <>
                {processBlock}
                {generating ? (
                  <div className="flex flex-col items-center gap-2 py-10 text-center" aria-live="polite">
                    <span className="flex gap-1" aria-hidden>
                      <i className="h-1.5 w-1.5 animate-bounce rounded-full bg-[#087b73]" />
                      <i className="h-1.5 w-1.5 animate-bounce rounded-full bg-[#087b73] [animation-delay:120ms]" />
                      <i className="h-1.5 w-1.5 animate-bounce rounded-full bg-[#087b73] [animation-delay:240ms]" />
                    </span>
                    <p className="text-sm font-medium text-[#183037]">行程生成中，稍等片刻…</p>
                    <p className="text-xs leading-relaxed text-[#5c7074]">
                      生成完成后可以在这里改行程、查车票
                    </p>
                  </div>
                ) : !processBlock ? (
                  <p className="py-10 text-center text-xs leading-relaxed text-[#8fa5a3]">
                    问行程、改时间、查车票、问美食，直接说一句就行
                  </p>
                ) : null}
              </>
            )
          }
          return (
            <>
              <ul className="space-y-4" aria-live="polite" aria-label="旅行助手消息">
                {before.map((m, i) => renderMsg(m, i, false))}
              </ul>
              {processBlock}
              {after.length > 0 && (
                <ul className="space-y-4" aria-label="旅行助手消息">
                  {after.map((m, i) => renderMsg(m, i, i === after.length - 1))}
                </ul>
              )}
            </>
          )
        })()}

        {clarificationOptions.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2" aria-label="选择规划天数">
            {clarificationOptions.map((option) => (
              <button key={option.label} type="button" disabled={inputDisabled}
                className="rounded-full border border-[#a9d1c9] bg-[#f5faf9] px-3 py-1.5 text-xs text-[#087b73] disabled:opacity-50"
                onClick={() => {
                  if (option.days === null) {
                    setFillingDays(true)
                    inputRef.current?.focus()
                  } else {
                    void send(option.message)
                  }
                }}>
                {option.label}
              </button>
            ))}
          </div>
        )}

        {pendingResponse?.itinerary && (
          <section className="mt-3 rounded-xl border border-[#a9d1c9] bg-[#f5faf9] p-3" aria-label="行程修改预览">
            <div className="flex items-start gap-2">
              <CheckCircle2 size={15} className="mt-0.5 shrink-0 text-[#087b73]" aria-hidden />
              <div className="min-w-0 flex-1">
                <h3 className="text-xs font-semibold text-[#183037]">
                  变更草案 v{pendingResponse.itinerary.plan_version}
                  {pendingChangeCount > 0 && ` · ${pendingChangeCount} 处调整`}
                </h3>
                <p className="mt-1 text-[10px] leading-relaxed text-[#5c7074]">
                  确认后生效；当前行程仍保持不变。
                </p>
                {pendingSummary ? (
                  <dl className="mt-2 space-y-1 text-[10px] leading-relaxed text-[#5c7074]">
                    {pendingSummary.briefFields.length > 0 && (
                      <div><dt className="inline text-[#8aa09c]">需求：</dt><dd className="inline">{pendingSummary.briefFields.join('、')}</dd></div>
                    )}
                    {pendingSummary.moved.length > 0 && (
                      <div><dt className="inline text-[#8aa09c]">移动：</dt><dd className="inline">{pendingSummary.moved.map((item) => `${item.name} 第${item.fromDay}天→第${item.toDay}天`).join('、')}</dd></div>
                    )}
                    {pendingSummary.briefFields.length === 0 && pendingSummary.moved.length === 0 && (
                      <div>行程内容有微调，应用后可在时间轴里对比。</div>
                    )}
                  </dl>
                ) : (
                  <p className="mt-2 text-[10px] text-[#5c7074]">这是当前会话的首份行程，应用后会作为当前版本。</p>
                )}
                <div className="mt-3 flex gap-2">
                  <button
                    type="button"
                    onClick={() => void applyPending()}
                    disabled={applying}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-[#087b73] px-4 py-1.5 text-[11px] font-medium text-white hover:bg-[#06655f] disabled:opacity-50"
                  >
                    {applying ? <Clock3 size={12} className="animate-pulse" /> : <Check size={12} />}
                    {applying ? '应用中…' : '应用'}
                  </button>
                  <button
                    type="button"
                    onClick={discardPending}
                    disabled={applying}
                    className="rounded-lg border border-[#c9dcd7] bg-white px-4 py-1.5 text-[11px] text-[#5c7074] hover:text-[#183037] disabled:opacity-50"
                  >
                    放弃
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
        {/* M2-h 快捷话术 chips（点即发送；忙时进排队槽） */}
        {!disabled && (
          <div className="mb-2 flex flex-wrap gap-1.5" aria-label="快捷话术">
            {quickChips.map((chip) => (
              <button
                key={chip.key}
                type="button"
                disabled={Boolean(pendingResponse)}
                onClick={() => {
                  if (chip.cityGuide) { onOpenCityGuide?.(); return }
                  void send(chip.text)
                }}
                className="cursor-pointer rounded-full border border-[#dae7e5] bg-white px-2.5 py-1 text-[11px] text-[#5c7074] transition-colors hover:border-[#087b73]/40 hover:text-[#087b73] disabled:cursor-not-allowed disabled:opacity-50"
              >
                {chip.label}
              </button>
            ))}
          </div>
        )}
        {/* M2-f 排队槽：忙时发出的消息落这里，可编辑可取消，本轮完成自动发 */}
        {queuedText && (loading || generating) && (
          <div className="mb-2 flex items-start gap-2 rounded-xl border border-dashed border-[#a9d1c9] bg-[#f5faf9] px-3 py-2">
            <Clock3 size={12} className="mt-1 shrink-0 text-[#087b73]" aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="text-[10px] text-[#5c7074]">本轮结束后自动发送（排队中，可编辑）</p>
              <input
                value={queuedText}
                onChange={(e) => setQueuedText(e.target.value)}
                aria-label="排队中的消息"
                className="mt-0.5 w-full bg-transparent text-xs text-[#183037] outline-none"
              />
            </div>
            <button
              type="button"
              onClick={() => setQueuedText('')}
              className="shrink-0 cursor-pointer text-[10px] text-[#8fa5a3] transition-colors hover:text-red-500"
            >
              取消
            </button>
          </div>
        )}
        <div className="flex items-end gap-2">
          {/* 预算圆圈：与 /agent 输入框同组件同位置（左簇第一位） */}
          <div className="pb-2">
            <BudgetRing status={budgetStatus} />
          </div>
          <textarea
            ref={inputRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={disabled}
            rows={1}
            placeholder={
              loading || generating ? '本轮进行中，先说下一条？结束后自动发送'
                : pendingResponse ? '请先应用或保留当前预览，再继续修改…'
                  : fillingDays ? '请输入想玩的天数，例如：4 天'
                    : hasItinerary ? '比如：第二天别排太满…' : '比如：杭州 3 天，2 个人，喜欢自然…'
            }
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
              title={loading || generating ? '排队：本轮结束后自动发送' : '发送'}
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
})

export default TravelChatDrawerImpl
