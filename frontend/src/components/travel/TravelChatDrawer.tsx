'use client'

/**
 * TravelChatDrawer — /travel 右侧「对话改行程」抽屉（2026-10-01）
 *
 * 为什么是抽屉而不是把输入框塞进表单：行程页的主体是**结构化结果**
 * （逐日时间轴 / 地图 / 导出），表单描述的是「一份新行程」。改单是
 * **在结果上做增量**——塞进表单就等于每次改一个数字都要重填一遍。
 *
 * 走的是与表单同一个端点 /api/travel/plan（见 src/api/travel.ts），
 * 关键差别只有一个：**复用同一个 conversation_id**。后端 PostgresSaver
 * checkpointer 按 thread_id 取回上一轮 brief 合并 + 指纹比对，需求变了才重排。
 * 因此：
 *   - 抽屉里说的话 → 后端合并进同一份行程 → 页面上的行程被替换（onResponse）
 *   - 后端只回追问（itinerary=null）→ 由 planState.applyPlanResponse 保留旧行程
 *     （一次追问不会把用户已经看到的行程清掉）
 *
 * 为什么不用 SSE 流式：域图是确定性规则规划（天气/路况/POI 校验），秒级
 * 返回完整 itinerary JSON；SSE 只有文本流，结构化行程（坐标/时刻/费用）
 * 拿不到，地图与导出都会失效。代价是没有逐字输出——所以这里用
 * 「正在按新需求重排…」的进行中提示 + 可中止来补体验。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Info, MessageSquarePlus, PlaneTakeoff, RotateCcw, Send, Square, X,
} from 'lucide-react'
import { planTravel, type PlanResponse } from '@/api/travel'
import MarkdownContent from '@/components/chat/MarkdownContent'
import { describePlanReply } from './planState'

interface ChatMsg {
  role: 'user' | 'assistant'
  text: string
  tag?: string
  tone?: 'ok' | 'warn'
}

interface Props {
  open: boolean
  onOpen: () => void
  onClose: () => void
  /** 当前行程的会话线程（= 后端 checkpoint 的 thread_id） */
  conversationId: string
  /** 页面上是否已经有行程 —— 只影响文案，不影响行为 */
  hasItinerary: boolean
  /** 拿到后端回复后交给页面合并（planState.applyPlanResponse） */
  onResponse: (data: PlanResponse) => void
  /** 开一份新行程（页面轮换 conversation_id → 本组件检测到变化后清空对话） */
  onStartNewTrip: () => void
  /** 预算硬额度已满：禁止再发起规划（与 /agent 输入框同一口径） */
  disabled?: boolean
  disabledHint?: string
}

/**
 * 快捷话术：**逐条对着后端槽位抽取的真实关键词写**（travel/models/brief.py
 * 的 PREFERENCE_KEYWORDS / PACE_KEYWORDS / DIET_KEYWORDS 与 requirement_agent
 * 的天数、预算正则），不是拍脑袋的示例 —— 写歪了用户点一下没反应，
 * 比不给提示还糟。
 */
const QUICK_PROMPTS = [
  '改成 3 天',
  '节奏轻松点',
  '预算 2000 元',
  '喜欢自然、美食',
  '不吃辣',
] as const

export default function TravelChatDrawer({
  open, onOpen, onClose, conversationId, hasItinerary, onResponse, onStartNewTrip,
  disabled, disabledHint,
}: Props) {
  const [messages, setMessages] = useState<ChatMsg[]>([])
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const prevConvRef = useRef(conversationId)

  // 换线程（= 开了新行程）→ 上一次的对话与在途请求都作废
  useEffect(() => {
    if (prevConvRef.current === conversationId) return
    prevConvRef.current = conversationId
    abortRef.current?.abort()
    setMessages([])
    setError('')
    setLoading(false)
  }, [conversationId])
  // 自动滚到底（新消息 / 进行中提示出现时）
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, loading, open])

  // Esc 收起（抽屉不挡主体，但键盘用户也需要一个出口）
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  // 卸载时中断在途请求（避免对已卸载组件 setState）
  useEffect(() => () => abortRef.current?.abort(), [])

  const send = useCallback(async (raw: string) => {
    const message = raw.trim()
    if (!message || loading || disabled) return
    // 记下发起时所属的线程：请求返回时若线程已换（用户点了「新行程」），
    // 这条回复属于旧行程，不能再往新对话里写。
    const sentConv = conversationId
    setError('')
    setText('')
    setMessages((prev) => [...prev, { role: 'user', text: message }])
    setLoading(true)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const data = await planTravel(message, conversationId, { signal: controller.signal })
      if (prevConvRef.current !== sentConv) return
      const { tag, tone } = describePlanReply(data)
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', text: data.final_answer || '（没有返回内容）', tag, tone },
      ])
      onResponse(data)
    } catch (e) {
      if (prevConvRef.current !== sentConv) return
      if (controller.signal.aborted) {
        // 用户主动中止：留一行痕迹，不当成错误
        setMessages((prev) => [...prev, { role: 'assistant', text: '（已中止这次调整）' }])
      } else {
        setError(e instanceof Error ? e.message : '调整失败，请稍后再试')
        // 输入框在请求期间是禁用的，用户不可能在改，原样还回去让他重发
        setText(message)
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      if (prevConvRef.current === sentConv) setLoading(false)
    }
  }, [conversationId, disabled, loading, onResponse])

  const stop = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setLoading(false)
  }, [])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void send(text)
    }
  }

  const hasMessages = messages.length > 0
  const inputDisabled = disabled || loading

  return (
    <>
      {/* 右侧边缘按钮：收起态常驻，点开变成对话框 */}
      {!open && (
        <button
          type="button"
          onClick={onOpen}
          className="fixed right-0 top-1/2 z-30 flex -translate-y-1/2 items-center gap-2 rounded-l-xl
            border border-r-0 border-border-subtle bg-surface-base py-3 pl-3 pr-2.5
            text-xs text-text-secondary shadow-card transition-colors
            hover:border-accent/40 hover:text-accent"
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
          border-l border-border-subtle bg-surface-base shadow-2xl
          transition-transform duration-300 ease-out
          ${open ? 'translate-x-0' : 'translate-x-full'}`}
      >
        {/* Header */}
        <div className="flex shrink-0 items-center gap-2.5 border-b border-border-subtle px-4 py-3">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent/10">
            <PlaneTakeoff size={17} className="text-accent" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-sm font-semibold text-text-primary">对话改行程</h2>
            <p className="truncate text-[10px] text-text-muted">
              {hasItinerary ? '在当前行程上继续调整，改完立即生效' : '直接说需求，我会出一份行程'}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              onClick={onStartNewTrip}
              title="开一份新行程（清空当前对话，不沿用旧约束）"
              aria-label="开一份新行程"
              className="flex h-7 items-center gap-1 rounded-lg border border-border-subtle px-2
                text-[11px] text-text-secondary transition-colors
                hover:border-accent/40 hover:text-text-primary"
            >
              <RotateCcw size={12} />
              新行程
            </button>
            <button
              type="button"
              onClick={onClose}
              title="收起对话框"
              aria-label="收起对话框"
              className="flex h-7 w-7 items-center justify-center rounded-lg text-text-secondary
                transition-colors hover:bg-surface-hover hover:text-text-primary"
            >
              <X size={16} />
            </button>
          </div>
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
          {hasMessages ? (
            <ul className="space-y-3">
              {messages.map((m, i) => (
                <li key={i} className={m.role === 'user' ? 'flex justify-end' : 'flex justify-start'}>
                  <div className={`max-w-[92%] rounded-2xl px-3.5 py-2 text-sm leading-relaxed ${
                    m.role === 'user'
                      ? 'bg-accent text-white'
                      : 'border border-border-subtle bg-surface-elevated text-text-primary'
                  }`}>
                    {m.role === 'user'
                      ? <p className="whitespace-pre-wrap break-words">{m.text}</p>
                      : <div className="markdown-body text-sm"><MarkdownContent content={m.text} /></div>}
                    {m.tag && (
                      <span className={`mt-2 inline-flex items-center rounded-full px-2 py-0.5 text-[10px] ${
                        m.tone === 'warn' ? 'bg-amber-100 text-amber-800' : 'bg-emerald-100 text-emerald-700'
                      }`}>
                        {m.tag}
                      </span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <div className="space-y-4">
              <div className="rounded-xl border border-dashed border-border-default bg-surface-elevated px-4 py-5">
                <p className="text-sm font-medium text-text-primary">
                  {hasItinerary ? '想怎么改？直接说一句就行' : '先说说想去哪、几天、几个人'}
                </p>
                <p className="mt-1 text-xs leading-relaxed text-text-muted">
                  改动会直接更新页面上的行程。说得越具体越准（比如「第二天别排太满」
                  「把预算压到 2000」）。同一标签页刷新后还能接着改。
                </p>
              </div>
              <div>
                <p className="mb-2 text-[11px] text-text-muted">常用说法</p>
                <div className="flex flex-wrap gap-2">
                  {QUICK_PROMPTS.map((p) => (
                    <button
                      key={p}
                      type="button"
                      disabled={inputDisabled}
                      onClick={() => void send(p)}
                      className="rounded-full border border-border-subtle bg-surface-base px-3 py-1.5 text-xs
                        text-text-secondary transition-colors hover:border-accent/40 hover:text-accent
                        disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {p}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {loading && (
            <div className="mt-3 flex items-center gap-2 text-xs text-text-muted">
              <span className="flex gap-0.5" aria-hidden>
                <i className="h-1 w-1 animate-bounce rounded-full bg-accent" />
                <i className="h-1 w-1 animate-bounce rounded-full bg-accent [animation-delay:120ms]" />
                <i className="h-1 w-1 animate-bounce rounded-full bg-accent [animation-delay:240ms]" />
              </span>
              正在按新需求重算行程（路况与天气为真实数据，约数秒）…
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
        </div>

        {/* 输入 */}
        <div className="shrink-0 border-t border-border-subtle px-4 py-3">
          <div className="flex items-end gap-2">
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={inputDisabled}
              rows={1}
              placeholder={hasItinerary ? '比如：第二天别排太满…' : '比如：杭州 3 天，2 个人，喜欢自然…'}
              aria-label="改行程的对话输入"
              className="max-h-32 min-w-0 flex-1 resize-none rounded-xl border border-border-subtle
                bg-surface-elevated px-4 py-2.5 text-sm text-text-primary placeholder:text-text-muted
                outline-none transition-colors focus:border-accent/50
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
                className="shrink-0 rounded-xl bg-accent p-2.5 text-white transition-colors
                  hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Send size={15} />
              </button>
            )}
          </div>
        </div>
      </div>
    </>
  )
}
