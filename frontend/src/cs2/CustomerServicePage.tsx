'use client'

// cs-workstation-v2 P1：用户端智能客服整页（P8 切换后挂 /customer-service）。
// 目标态对照 docs/tasks/cs-workstation-v2/prototypes/user-portal.html 的客服视图；
// 聊天链路复用既有 SSE 契约（/api/chat/customer-service/stream，含断线续播与中止）。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Headphones } from 'lucide-react'
import { abortChat, streamChat } from '@/api/chat'
import { nanoid } from 'nanoid'
import {
  applySseEvent,
  beginTurn,
  createTyper,
  makeId,
} from './chatReducer'
import {
  fetchCustomerContext,
  fetchMyConversations,
  fetchMyMessages,
  newSessionId,
  postMyMessage,
  requestHandoff,
  submitRating,
  ORDER_STATUS_ZH,
  type CustomerContext,
  type MyConversationItem,
  type MyMessageRow,
} from './cs2Api'
import { newConversationDraft, type Cs2ConversationDraft, type Cs2Message } from './types'
import SiteNav from '@/mall/SiteNav'
import ConfirmCard from './ConfirmCard'
import FoldCards from './FoldCards'

const QUICK_ENTRIES = [
  { icon: '📦', title: '查询订单', hint: 'DEMO-1001 的订单状态', text: '帮我查一下 DEMO-1001 的订单状态' },
  { icon: '🚚', title: '物流追踪', hint: 'DEMO-1003 的物流进度', text: 'DEMO-1003 的物流到哪了？' },
  { icon: '💰', title: '申请退款', hint: 'DEMO-1012 未发货退款', text: '我要退掉 DEMO-1012 的耳机' },
  { icon: '📝', title: '投诉建议', hint: '物流时效投诉', text: '我要投诉，物流太慢了' },
] as const

/** 后端 sender_type → cs2 角色 */
function roleOf(senderType: string): Cs2Message['role'] {
  if (senderType === 'human_agent') return 'agent'
  if (senderType === 'user') return 'user'
  if (senderType === 'system') return 'system'
  return 'assistant'
}

function sessionFromItem(item: MyConversationItem): Cs2ConversationDraft {
  const draft = newConversationDraft(item.conversation_id)
  draft.title = item.summary?.slice(0, 24) || `会话 ${item.conversation_id.slice(0, 6)}`
  draft.status = item.conversation_status
  draft.messages = (item.messages ?? []).map((m) => ({
    id: m.message_id || makeId(),
    role: roleOf(m.sender_type),
    content: m.content,
    createdAt: Date.parse(m.created_at) || Date.now(),
  }))
  return draft
}

export default function CustomerServicePage() {
  const [sessions, setSessions] = useState<Cs2ConversationDraft[]>([])
  const [draft, setDraft] = useState<Cs2ConversationDraft>(() => newConversationDraft())
  const [input, setInput] = useState('')
  const [revealing, setRevealing] = useState(false)
  const [customerCtx, setCustomerCtx] = useState<CustomerContext | null>(null)
  const [handoffState, setHandoffState] = useState<string>('none')
  const [handoffMeta, setHandoffMeta] = useState<{
    total_deadline_at: string | null
    queue_position: number
  } | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const [foldKey, setFoldKey] = useState(0)

  const handoffStateRef = useRef(handoffState)
  handoffStateRef.current = handoffState
  const pollSeenRef = useRef(new Set<string>())

  const abortRef = useRef<AbortController | null>(null)
  const typerRef = useRef<ReturnType<typeof createTyper> | null>(null)
  const requestIdRef = useRef<string>('')
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const draftRef = useRef(draft)
  draftRef.current = draft

  const busy = draft.streaming || revealing

  const patchDraft = useCallback((fn: (d: Cs2ConversationDraft) => void) => {
    setDraft((prev) => {
      const next: Cs2ConversationDraft = {
        ...prev,
        messages: prev.messages.map((m) => ({ ...m })),
      }
      fn(next)
      return next
    })
  }, [])

  useEffect(() => {
    let cancelled = false
    // P2：从商城页「咨询客服/申请退款」跳转预填输入框
    const prefill = window.sessionStorage.getItem('cs2-prefill')
    if (prefill) {
      window.sessionStorage.removeItem('cs2-prefill')
      setInput(prefill)
    }
    fetchMyConversations(10)
      .then((items) => {
        if (cancelled) return
        setSessions(items.map(sessionFromItem))
      })
      .catch(() => {})
    fetchCustomerContext()
      .then((ctx) => {
        if (!cancelled) setCustomerCtx(ctx)
      })
      .catch(() => {})
    return () => {
      cancelled = true
      abortRef.current?.abort()
    }
  }, [])

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [draft.messages, draft.liveTimeline])

  const runTurn = useCallback(
    async (question: string) => {
      const sessionDraft = draftRef.current
      const sessionId = sessionDraft.id === 'new' ? newSessionId() : sessionDraft.id
      if (sessionDraft.id === 'new') {
        patchDraft((d) => {
          d.id = sessionId
          d.title = question.slice(0, 24)
        })
      }
      patchDraft((d) => {
        d.messages.push({
          id: makeId(),
          role: 'user',
          content: question,
          createdAt: Date.now(),
        })
        beginTurn(d)
      })

      const controller = new AbortController()
      abortRef.current = controller
      const requestId = nanoid(21)
      requestIdRef.current = requestId
      const typer = createTyper((shown) => {
        patchDraft((d) => {
          for (let i = d.messages.length - 1; i >= 0; i -= 1) {
            if (d.messages[i].role === 'assistant') {
              d.messages[i].content = shown
              break
            }
          }
        })
      })
      typerRef.current = typer

      // 流式阶段：内容先进缓冲（UI 显示 typing 点点 + live 时间线）；
      // done 后本地逐字揭示，形成打字机效果（CS 后端为兜底单帧全量 delta）。
      let buffer = ''
      let terminal = false
      try {
        for await (const evt of streamChat(
          { question, session_id: sessionId, request_id: requestId },
          controller.signal,
          'customer-service',
        )) {
          if (evt.event === 'delta') {
            buffer += evt.data.content ?? ''
            continue
          }
          if (evt.event === 'done') {
            if (!buffer && evt.data.answer) buffer = evt.data.answer
          }
          patchDraft((d) => {
            applySseEvent(d, evt)
          })
          if (evt.event === 'done' || evt.event === 'error' || evt.event === 'paused') {
            terminal = true
          }
        }
      } catch {
        // 断流已由 streamChat 内部 resume 兜底；走到这里多为主动中止
      }

      const lastDraft = draftRef.current
      const lastMsg = [...lastDraft.messages].reverse().find((m) => m.role === 'assistant')
      if (terminal && lastMsg && lastMsg.role === 'assistant' && !lastMsg.interrupted) {
        setRevealing(true)
        await typer.run(buffer)
        setRevealing(false)
      }
      patchDraft((d) => {
        for (let i = d.messages.length - 1; i >= 0; i -= 1) {
          if (d.messages[i].role === 'assistant') {
            d.messages[i].streaming = false
            break
          }
        }
        d.streaming = false
      })
      abortRef.current = null
      typerRef.current = null
    },
    [patchDraft],
  )

  const send = useCallback(
    (text: string) => {
      const t = text.trim()
      if (!t || busy) return
      // P3：人工服务态消息直达坐席（不进 AI 图），其余走 SSE 聊天流
      if (['waiting_human', 'agent_offered', 'human_active'].includes(handoffStateRef.current)) {
        const id = draftRef.current.id
        if (id === 'new') return
        setInput('')
        patchDraft((d) => {
          d.messages.push({
            id: makeId(),
            role: 'user',
            content: t,
            createdAt: Date.now(),
          })
        })
        void postMyMessage(id, t).catch(() => {
          patchDraft((d) => {
            d.messages.push({
              id: makeId(),
              role: 'system',
              content: '消息发送失败，请稍后重试。',
              createdAt: Date.now(),
            })
          })
        })
        return
      }
      setInput('')
      void runTurn(t)
    },
    [busy, patchDraft, runTurn],
  )

  const stop = useCallback(() => {
    typerRef.current?.skip()
    abortRef.current?.abort()
    const sid = draftRef.current.id
    if (sid !== 'new' && requestIdRef.current) {
      void abortChat(sid, requestIdRef.current, 'customer-service')
    }
    setRevealing(false)
    patchDraft((d) => {
      d.streaming = false
      for (let i = d.messages.length - 1; i >= 0; i -= 1) {
        if (d.messages[i].role === 'assistant') {
          d.messages[i].streaming = false
          break
        }
      }
    })
  }, [patchDraft])

  const selectSession = useCallback((id: string) => {
    const found = sessions.find((s) => s.id === id)
    if (found) {
      pollSeenRef.current = new Set(found.messages.map((m) => m.id))
      setDraft({ ...found, messages: found.messages.map((m) => ({ ...m })) })
      setHandoffState('none')
      setHandoffMeta(null)
    }
  }, [sessions])

  const startNewSession = useCallback(() => {
    abortRef.current?.abort()
    typerRef.current?.skip()
    setRevealing(false)
    setDraft(newConversationDraft())
    setHandoffState('none')
    setHandoffMeta(null)
  }, [])

  const requestHuman = useCallback(async () => {
    const id = draftRef.current.id
    if (id === 'new' || !draftRef.current.messages.length) return
    try {
      const res = await requestHandoff(id)
      setHandoffState(res.handoff_state ?? 'requested')
    } catch {
      /* 转人工失败不打断对话 */
    }
  }, [])

  // P3：人工服务态轮询——handoff 元信息（4 态/倒计时/排队位）+ 坐席消息增量
  useEffect(() => {
    const conversationId = draft.id
    if (conversationId === 'new') return
    if (!['handoff_requested', 'waiting_human', 'agent_offered', 'human_active'].includes(handoffState)) {
      return
    }
    let cancelled = false
    const poll = async () => {
      try {
        const res = await fetchMyMessages(conversationId, 0, 50)
        if (cancelled) return
        setHandoffState(res.handoff_state ?? 'none')
        if (res.handoff_meta) {
          setHandoffMeta({
            total_deadline_at: res.handoff_meta.total_deadline_at,
            queue_position: res.handoff_meta.queue_position,
          })
        }
        const incoming = (res.messages ?? []).filter(
          (m: MyMessageRow) => !pollSeenRef.current.has(m.message_id),
        )
        for (const m of incoming) pollSeenRef.current.add(m.message_id)
        const agentMsgs = incoming
          .filter((m) => m.sender_type === 'human_agent' || m.sender_type === 'system')
          .map((m) => ({
            id: m.message_id,
            role: (m.sender_type === 'human_agent' ? 'agent' : 'system') as 'agent' | 'system',
            content: m.content,
            createdAt: Date.parse(m.created_at) || Date.now(),
          }))
        if (agentMsgs.length) {
          patchDraft((d) => {
            d.messages.push(...agentMsgs)
          })
        }
      } catch {
        /* 轮询失败静默，下一轮重试 */
      }
    }
    void poll()
    const timer = setInterval(poll, 3000)
    const clock = setInterval(() => setNow(Date.now()), 1000)
    return () => {
      cancelled = true
      clearInterval(timer)
      clearInterval(clock)
    }
  }, [draft.id, handoffState, patchDraft])

  const rate = useCallback(
    async (star: number, note: string) => {
      const id = draftRef.current.id
      if (id !== 'new' && star >= 1) {
        try {
          await submitRating(id, star, note)
        } catch {
          /* 评分失败静默，避免打断致谢 */
        }
      }
      patchDraft((d) => {
        d.rated = true
      })
    },
    [patchDraft],
  )

  const showWelcome = draft.messages.length === 0 && !busy
  const lastMsg = draft.messages[draft.messages.length - 1]
  const showCsat =
    draft.id !== 'new' && !draft.rated && !busy && !draft.candidateOptions &&
    !!lastMsg && lastMsg.role === 'assistant' && !lastMsg.streaming

  const convOptions = useMemo(
    () =>
      sessions.map((s) => ({
        id: s.id,
        label: `${s.title}（${s.status === 'closed' ? '已结束' : '进行中'}）`,
      })),
    [sessions],
  )

  return (
    <div className="flex h-screen flex-col bg-slate-100">
      <SiteNav />
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
          {/* 会话头 */}
          <header className="flex h-12 flex-none items-center gap-2 border-b border-slate-200 bg-white px-4">
            <Headphones size={16} className="text-indigo-600" aria-hidden />
            <b className="text-sm text-slate-900">智能客服</b>
            <select
              className="h-7 max-w-56 rounded-lg border border-slate-200 bg-white px-2 text-xs text-slate-700"
              aria-label="切换会话"
              value={draft.id === 'new' ? '' : draft.id}
              onChange={(e) => selectSession(e.target.value)}
            >
              <option value="">新的会话</option>
              {convOptions.map((o) => (
                <option key={o.id} value={o.id}>{o.label}</option>
              ))}
            </select>
            <button
              type="button"
              className="rounded-lg border border-slate-200 bg-white px-3 py-1 text-xs text-slate-700 hover:bg-slate-50 disabled:opacity-45"
              disabled={draft.id === 'new' || draft.messages.length === 0}
              onClick={() => void requestHuman()}
            >
              转人工
            </button>
            <button
              type="button"
              className="ml-auto rounded-lg px-3 py-1 text-xs text-slate-500 hover:bg-slate-50"
              onClick={startNewSession}
            >
              + 新会话
            </button>
          </header>

          {/* 消息流 */}
          <div ref={scrollRef} className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-auto px-4 py-3.5">
            {showWelcome && (
              <div className="mx-auto w-full max-w-md rounded-xl border border-slate-200 bg-white p-3.5 text-center">
                <span className="mb-2 inline-flex items-center gap-1 rounded-full bg-indigo-50 px-2.5 py-0.5 text-[11.5px] text-indigo-600">
                  🤖 AI 智能客服
                </span>
                <h3 className="mb-1.5 text-[15px] text-slate-900">您好，我是演示商城的智能客服</h3>
                <p className="mb-2.5 text-xs leading-relaxed text-slate-500">
                  回复由人工智能生成，仅供参考，请注意甄别真实性。
                </p>
                <div className="mb-2.5 rounded-lg bg-amber-50 px-2.5 py-1 text-[11.5px] text-amber-600">
                  🧪 演示沙盒：使用演示业务数据，可安全体验全部流程
                </div>
                <div className="mb-2.5 grid grid-cols-2 gap-2">
                  {QUICK_ENTRIES.map((q) => (
                    <button
                      key={q.title}
                      type="button"
                      className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2 text-left text-xs text-slate-700 hover:border-indigo-400"
                      onClick={() => send(q.text)}
                    >
                      <b className="block">{q.icon} {q.title}</b>
                      <span className="text-[11px] text-slate-500">{q.hint}</span>
                    </button>
                  ))}
                </div>
                <button
                  type="button"
                  className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-500"
                  disabled
                >
                  👤 转接人工（请先发送一条消息）
                </button>
              </div>
            )}

            {draft.messages.map((m) => (
              <MessageBubble key={m.id} message={m} />
            ))}

            {/* live 时间线（完成后固化到气泡下） */}
            {draft.streaming && draft.liveTimeline.length > 0 && (
              <div className="self-center rounded-full border border-dashed border-slate-200 bg-slate-50 px-3 py-1 text-[11px] text-slate-500">
                本轮处理进度：
                {draft.liveTimeline.map((n, i) => (
                  <span key={`${n}-${i}`} className={i === draft.liveTimeline.length - 1 ? 'text-indigo-600' : ''}>
                    {i > 0 ? ' → ' : ''}{n}
                  </span>
                ))}
              </div>
            )}

            {/* 流式中无内容的 typing 点点 */}
            {draft.streaming && !(lastMsg && lastMsg.role === 'assistant' && lastMsg.content) && (
              <div className="self-start px-1 text-xs text-slate-400">
                正在输入
                <span className="ml-1 inline-flex gap-0.5">
                  <i className="h-1 w-1 animate-bounce rounded-full bg-slate-400" />
                  <i className="h-1 w-1 animate-bounce rounded-full bg-slate-400 [animation-delay:150ms]" />
                  <i className="h-1 w-1 animate-bounce rounded-full bg-slate-400 [animation-delay:300ms]" />
                </span>
              </div>
            )}

            {/* 候选点选（clarification 事件） */}
            {draft.candidateOptions && (
              <div className="mx-auto w-full max-w-md rounded-xl bg-blue-50 p-3 text-xs text-slate-700">
                <b>{draft.clarificationQuestion ?? '请选择要办理的内容'}</b>
                <div className="mt-2 flex flex-col gap-1.5">
                  {draft.candidateOptions.map((o, i) => (
                    <button
                      key={o.id}
                      type="button"
                      className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-left text-xs hover:border-indigo-400"
                      onClick={() => send(`${i + 1}. ${o.label}`)}
                    >
                      {i + 1}. {o.label}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* 确认卡（P2：退款/取消两类，有效期倒计时 + 修改原因 + 幂等确认） */}
            {draft.id !== 'new' && !busy && (
              <ConfirmCard
                conversationId={draft.id}
                onReceipt={(text) => {
                  patchDraft((d) => {
                    d.messages.push({
                      id: makeId(),
                      role: 'system',
                      content: text,
                      createdAt: Date.now(),
                    })
                  })
                  setFoldKey((k) => k + 1)
                }}
              />
            )}

            {/* 满意度卡 */}
            {showCsat && <SatisfactionCard onRate={rate} />}

            {/* 折叠卡：我的退款申请 / 我的工单 */}
            {draft.id !== 'new' && <FoldCards refreshKey={foldKey} />}

            {/* 转人工 4 态横幅（P3）：requested/waiting（倒计时+排队位）/
                offered / active（接管后可继续输入）/ closed */}
            {['handoff_requested', 'waiting_human', 'agent_offered', 'human_active'].includes(handoffState) && (
              <div
                className={`mx-auto w-full max-w-md rounded-lg px-3 py-2 text-xs ${
                  handoffState === 'human_active'
                    ? 'bg-emerald-50 text-emerald-600'
                    : handoffState === 'agent_offered'
                      ? 'bg-blue-50 text-blue-600'
                      : 'bg-amber-50 text-amber-600'
                }`}
              >
                {handoffState === 'handoff_requested' && '正在请求转接人工客服…'}
                {handoffState === 'agent_offered' && '已为您指派人工客服，正在等待其接单…'}
                {handoffState === 'waiting_human' && '等待人工客服接入…'}
                {handoffState === 'human_active' && '人工客服已接入，请直接输入您的问题'}
                {(handoffState === 'waiting_human' || handoffState === 'handoff_requested') && (
                  <span className="ml-1.5 inline-flex animate-pulse">•••</span>
                )}
                {handoffState === 'waiting_human' && handoffMeta?.total_deadline_at && (
                  <div className="mt-1">
                    ⏳ 剩余 {formatCountdown(handoffMeta.total_deadline_at, now)}
                    {typeof handoffMeta.queue_position === 'number' &&
                      ` · 前面 ${handoffMeta.queue_position} 位等待`}
                  </div>
                )}
                {handoffState === 'human_active' && (
                  <div className="mt-1">消息将直达为您服务的人工客服</div>
                )}
              </div>
            )}
            {handoffState === 'closed' && (
              <div className="mx-auto w-full max-w-md rounded-lg bg-slate-100 px-3 py-2 text-xs text-slate-500">
                人工服务已结束，AI 客服继续为您服务。
              </div>
            )}
          </div>

          {/* 状态栏 */}
          <div className="flex h-8 flex-none items-center gap-2 px-4 text-xs text-slate-500">
            {busy && <span className="h-2 w-2 animate-pulse rounded-full bg-indigo-500" aria-hidden />}
            {busy && <span>正在 {draft.liveStep ?? '处理'}…</span>}
            {draft.pausedText && <span className="text-amber-600">{draft.pausedText}</span>}
          </div>

          {/* 输入区 */}
          <div className="flex flex-none items-end gap-2 border-t border-slate-200 bg-white px-3.5 pb-3 pt-2">
            <textarea
              className="min-h-11 max-h-32 flex-1 resize-none rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-base text-slate-900 outline-none focus:border-indigo-500"
              rows={1}
              placeholder="请描述您的问题…（Enter 发送）"
              aria-label="咨询输入框"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  if (busy) return
                  send(input)
                }
              }}
            />
            <button
              type="button"
              className={`min-h-11 rounded-xl px-4 text-sm text-white ${busy ? 'bg-red-500' : 'bg-indigo-600 hover:opacity-90'}`}
              onClick={() => (busy ? stop() : send(input))}
            >
              {busy ? '停止' : '发送'}
            </button>
          </div>
        </div>

        {/* 右栏：客户资料 + 最近订单 */}
        <aside className="hidden w-72 flex-none overflow-auto border-l border-slate-200 bg-white p-3 lg:block">
          <div className="mb-2.5 rounded-xl border border-slate-200 p-3">
            <div className="mb-1.5 flex items-center gap-2">
              <h4 className="text-xs text-slate-700">客户资料</h4>
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-500">模拟数据</span>
            </div>
            <p className="mb-2 text-[11.5px] text-slate-500">助手可参考当前账号的订单信息回答问题</p>
            {customerCtx ? (
              <>
                <div className="flex items-center gap-2.5">
                  <span className="grid h-8 w-8 place-items-center rounded-full bg-indigo-50 text-sm text-indigo-600">
                    {customerCtx.customer.display_name.slice(0, 1)}
                  </span>
                  <div>
                    <b className="text-xs text-slate-900">{customerCtx.customer.display_name}</b>
                    <div className="text-[11px] text-slate-500">
                      {customerCtx.customer.level === 'gold' ? '黄金会员' : (customerCtx.customer.level ?? '普通会员')}
                      {customerCtx.customer.register_time ? ` · 注册 ${customerCtx.customer.register_time.slice(0, 10)}` : ''}
                    </div>
                  </div>
                </div>
                {/* P2 资金闭环：余额 + 充值 + 最近流水（只读快照） */}
                {customerCtx.balance && (
                  <div className="mt-2.5 flex items-center justify-between gap-2 rounded-xl bg-indigo-50 px-2.5 py-2">
                    <div>
                      <span className="text-[11px] text-slate-500">账户余额</span>
                      <br />
                      <b className="text-[15px] text-indigo-600">
                        ¥{Number(customerCtx.balance.balance).toFixed(2)}
                      </b>
                    </div>
                    <button
                      type="button"
                      className="rounded-lg bg-indigo-600 px-2 py-1 text-[11px] text-white hover:opacity-90"
                      onClick={() => {
                        void (async () => {
                          try {
                            const { topUp } = await import('@/mall/api')
                            const wallet = await topUp(1000)
                            setCustomerCtx({
                              ...customerCtx,
                              balance: {
                                balance: wallet.balance,
                                currency: wallet.currency,
                                ledger: wallet.ledger,
                              },
                            })
                          } catch {
                            /* 充值失败不打断会话 */
                          }
                        })()
                      }}
                    >
                      充值 ¥1,000
                    </button>
                  </div>
                )}
                {customerCtx.balance && customerCtx.balance.ledger.length > 0 && (
                  <div className="mt-1.5 text-[11px] text-slate-500">
                    资金流水（最近 {customerCtx.balance.ledger.length} 笔）
                    {customerCtx.balance.ledger.map((l, i) => (
                      <div key={i} className="flex items-center justify-between gap-2 border-b border-dashed border-slate-100 py-0.5 last:border-b-0">
                        <span className="min-w-0 truncate text-slate-600">{l.memo || l.kind}</span>
                        <b className={l.direction === 'in' ? 'text-emerald-600' : 'text-slate-700'}>
                          {l.direction === 'in' ? '+' : '−'}¥{Number(l.amount).toFixed(2)}
                        </b>
                      </div>
                    ))}
                  </div>
                )}
                <div className="mt-2.5">
                  <div className="mb-1 text-[11.5px] text-slate-500">最近订单</div>
                  {customerCtx.orders.map((o) => (
                    <div key={o.order_no} className="mb-1.5 flex items-center gap-2 rounded-lg border border-slate-100 px-2.5 py-2 text-xs">
                      <div className="min-w-0 flex-1">
                        <b className="block text-xs text-slate-900">{o.order_no}</b>
                        <span className="block truncate text-[11px] text-slate-500">{o.product_summary}</span>
                      </div>
                      <div className="text-right">
                        <div className="text-slate-700">{o.total_amount != null ? `¥${Number(o.total_amount).toFixed(2)}` : ''}</div>
                        <span className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[10.5px] text-slate-500">
                          {ORDER_STATUS_ZH[o.status] ?? o.status}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <p className="text-[11.5px] text-slate-400">客户资料加载中…</p>
            )}
          </div>
          <div className="rounded-lg bg-amber-50 px-2.5 py-1.5 text-[11.5px] leading-relaxed text-amber-600">
            当前环境使用模拟业务数据，订单信息仅用于体验客服查询和售后流程。
          </div>
        </aside>
      </div>
    </div>
  )
}

function formatCountdown(deadlineIso: string, now: number): string {
  const ts = Date.parse(deadlineIso)
  if (!Number.isFinite(ts)) return '--:--'
  const s = Math.max(0, Math.floor((ts - now) / 1000))
  const m = Math.floor(s / 60)
  const ss = s % 60
  return `${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}`
}

function MessageBubble({ message }: { message: Cs2Message }) {
  const who =
    message.role === 'user'
      ? '我'
      : message.role === 'agent'
        ? '人工客服'
        : message.role === 'system'
          ? '系统'
          : 'AI 客服 · 由人工智能生成'
  const align =
    message.role === 'user'
      ? 'self-end'
      : message.role === 'system'
        ? 'self-center max-w-[88%]'
        : 'self-start'
  const bubble =
    message.role === 'user' || message.role === 'agent'
      ? 'rounded-xl bg-emerald-50 px-3 py-2'
      : message.role === 'system'
        ? 'rounded-full border border-dashed border-slate-200 px-3 py-1 text-xs text-slate-500'
        : 'rounded-xl border border-slate-200 bg-white px-3 py-2'

  return (
    <div className={`flex max-w-[78%] flex-col gap-0.5 ${align}`}>
      {message.role !== 'system' && (
        <div className={`flex items-center gap-1.5 text-[11px] text-slate-400 ${message.role === 'user' ? 'justify-end' : ''}`}>
          {who}
          {message.interrupted && <span className="rounded-full bg-slate-100 px-1.5 text-[10px]">已停止</span>}
        </div>
      )}
      <div className={`whitespace-pre-wrap text-[13px] leading-relaxed text-slate-800 ${bubble}`}>
        {message.content || (message.streaming ? '' : '（空）')}
      </div>
      {message.csNodes && message.csNodes.length > 0 && (
        <div className="flex flex-wrap items-center gap-1 pt-0.5 text-[10.5px] text-slate-400">
          本轮处理进度：
          {message.csNodes.map((n, i) => (
            <span key={`${n}-${i}`} className="rounded-full border border-slate-100 bg-slate-50 px-1.5 py-px">
              {i > 0 ? '· ' : ''}{n}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function SatisfactionCard({ onRate }: { onRate: (star: number, note: string) => void }) {
  const [hover, setHover] = useState(0)
  const [picked, setPicked] = useState(0)
  const [note, setNote] = useState('')
  const [done, setDone] = useState(false)

  if (done) {
    return (
      <div className="mx-auto w-full max-w-md rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-600">
        感谢您的评价（{picked} 星{note ? ` · ${note}` : ''}），已计入 CSAT 统计。
      </div>
    )
  }
  return (
    <div className="mx-auto w-full max-w-md rounded-xl border border-slate-200 bg-white p-3 text-xs">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <b className="text-slate-900">请为本次服务评价</b>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-500">1-5 星 · 选填备注</span>
        <button
          type="button"
          className="ml-auto text-[11px] text-slate-400 hover:text-slate-600"
          onClick={() => {
            setPicked(0)
            setDone(true)
            onRate(0, note)
          }}
        >
          暂不评价
        </button>
      </div>
      <div className="flex gap-0.5" role="group" aria-label="满意度星级">
        {[1, 2, 3, 4, 5].map((n) => (
          <button
            key={n}
            type="button"
            aria-label={`${n} 星`}
            className="px-0.5 text-xl leading-none text-slate-300 hover:text-amber-400"
            style={{ color: n <= (hover || picked) ? '#f59e0b' : undefined }}
            onMouseEnter={() => setHover(n)}
            onMouseLeave={() => setHover(0)}
            onClick={() => {
              setPicked(n)
              setDone(true)
              onRate(n, note)
            }}
          >
            ★
          </button>
        ))}
      </div>
      <input
        className="mt-2 h-8 w-full rounded-lg border border-slate-200 bg-slate-50 px-2.5 text-xs text-slate-700"
        maxLength={200}
        placeholder="选填备注（≤200 字）"
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
    </div>
  )
}
