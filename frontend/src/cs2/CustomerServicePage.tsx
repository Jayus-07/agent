'use client'

// cs-workstation-v2 P1：用户端智能客服整页（P8 切换后挂 /customer-service）。
// 目标态对照 docs/tasks/cs-workstation-v2/prototypes/user-portal.html 的客服视图；
// 聊天链路复用既有 SSE 契约（/api/chat/customer-service/stream，含断线续播与中止）。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Headphones, Package, Send, Square } from 'lucide-react'
import Link from 'next/link'
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
import ConfirmCard from './ConfirmCard'
import FoldCards from './FoldCards'
import OrderPickerSheet from './OrderPickerSheet'
import { money, type MallOrder } from '@/mall/api'
import SiteNav from '@/mall/SiteNav'

// 快捷入口话术不携带具体订单号：订单号由懒种子按用户生成（MO- 前缀、
// 无法预知），通用话术会触发后端候选点选/订单列表，任何账号都能走通
//（2026-11-03 修复：原 DEMO-1001 等硬编码号在 http 网关下必然查无此单）。
const QUICK_ENTRIES = [
  { icon: '📦', title: '查询订单', hint: '查看我的近期订单', text: '帮我查一下我的订单状态' },
  { icon: '🚚', title: '物流追踪', hint: '查询订单物流进度', text: '帮我查一下订单的物流进度' },
  { icon: '💰', title: '申请退款', hint: '为订单申请退款', text: '我要申请退款' },
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
    // D7：订单卡等结构化消息随回放透传，渲染层按 card+order_ref 出卡
    contentType: m.content_type,
    attachments: m.attachments ?? null,
  }))
  return draft
}

export default function CustomerServicePage() {
  const [sessions, setSessions] = useState<Cs2ConversationDraft[]>([])
  const [draft, setDraft] = useState<Cs2ConversationDraft>(() => newConversationDraft())
  const [input, setInput] = useState('')
  const [revealing, setRevealing] = useState(false)
  const [customerCtx, setCustomerCtx] = useState<CustomerContext | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [handoffState, setHandoffState] = useState<string>('none')
  const [handoffMeta, setHandoffMeta] = useState<{
    total_deadline_at: string | null
    queue_position: number
  } | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const [foldKey, setFoldKey] = useState(0)
  // M1：订单选择抽屉（淘宝式「发订单给客服」）
  const [orderSheetOpen, setOrderSheetOpen] = useState(false)
  const [preselectOrderNo, setPreselectOrderNo] = useState<string | null>(null)

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
    // 商城「我的订单」深链：带订单号直达订单抽屉（仅预选高亮，需用户点发送确认）
    const orderRef = window.sessionStorage.getItem('cs2-order-ref')
    if (orderRef) {
      window.sessionStorage.removeItem('cs2-order-ref')
      setPreselectOrderNo(orderRef)
      setOrderSheetOpen(true)
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
    async (
      question: string,
      opts?: {
        orderNo?: string
        /** D7 补口：AI 态本地消息也带结构化字段，订单卡即时渲染（请求侧仍只传 order_no） */
        attachments?: Array<{ type: string; order_no?: string }>
      },
    ) => {
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
          // D7 补口：AI 态本地消息与人工态/回放同构，带附件时即时渲染订单卡
          contentType: opts?.attachments?.length ? 'card' : 'text',
          attachments: opts?.attachments ?? null,
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
          {
            question,
            session_id: sessionId,
            request_id: requestId,
            ...(opts?.orderNo ? { order_no: opts.orderNo } : {}),
          },
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
    (
      text: string,
      opts?: {
        orderNo?: string
        attachments?: Array<{ type: string; order_no?: string }>
      },
    ) => {
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
            // D7：带附件的本地消息即时按 card 渲染（与服务端落库形态一致）
            contentType: opts?.attachments?.length ? 'card' : 'text',
            attachments: opts?.attachments ?? null,
          })
        })
        void postMyMessage(id, t, opts?.attachments).catch(() => {
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
      void runTurn(t, {
        orderNo: opts?.orderNo,
        attachments: opts?.attachments,
      })
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

  // 空回复重试：用上一条用户话术重新发起一轮（仅 AI 服务态可用，人工态由渲染条件拦截）
  const retryLast = useCallback(() => {
    if (busy) return
    const prev = [...draftRef.current.messages].reverse().find((m) => m.role === 'user')
    if (prev?.content) send(prev.content)
  }, [busy, send])

  const selectSession = useCallback((id: string) => {
    const found = sessions.find((s) => s.id === id)
    if (found) {
      pollSeenRef.current = new Set(found.messages.map((m) => m.id))
      setDraft({ ...found, messages: found.messages.map((m) => ({ ...m })) })
      setHandoffState('none')
      setHandoffMeta(null)
      // D3（2026-11-22 实机验证）：handoff 状态此前只在「转人工响应」和
      // 「4 态轮询」两个来源里，刷新/切换会话后丢成 'none'，排队/已接入/
      // 已结束横幅全部消失。选择会话时从服务端恢复一次（竞态防护：快速
      // 切换时只接受仍指向当前会话的响应）。
      fetchMyMessages(id, 0, 1)
        .then((res) => {
          if (draftRef.current.id !== id) return
          setHandoffState(res.handoff_state ?? 'none')
          if (res.handoff_meta) {
            setHandoffMeta({
              total_deadline_at: res.handoff_meta.total_deadline_at,
              queue_position: res.handoff_meta.queue_position,
            })
          }
        })
        .catch(() => {
          /* 恢复失败静默：轮询 effect 会按既有逻辑接手 */
        })
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
            // D7：结构化消息（card+order_ref）字段随增量透传
            contentType: m.content_type,
            attachments: m.attachments ?? null,
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

  // M2：发订单给客服——结构化文本 + 服务端订单引用（order_no 校验归属后
  // 注入会话订单槽；AI 态经 streamChat 透传，人工态经 postMyMessage 附件），
  // 用户消息落库 content_type=card + [order_ref]，坐席端渲染订单卡
  const sendOrderRef = useCallback(
    (order: MallOrder) => {
      setOrderSheetOpen(false)
      // 发送后清预选：否则下次打开抽屉，上一单仍带 aria-selected 残留高亮（#508）
      setPreselectOrderNo(null)
      send(
        `【订单咨询】订单号 ${order.order_no}｜${order.product_summary}｜${money(order.total_amount)}｜${ORDER_STATUS_ZH[order.status] ?? order.status}`,
        {
          orderNo: order.order_no,
          attachments: [{ type: 'order_ref', order_no: order.order_no }],
        },
      )
    },
    [send],
  )
  const sendSheetText = useCallback(
    (text: string) => {
      setOrderSheetOpen(false)
      send(text)
    },
    [send],
  )

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
  // 满意度评价只在有人工介入的会话结束后出现（纯 AI 自动回复轮次不弹，
  // 对齐商城客服惯例：AI 对话轮次不打扰用户）；评分或跳过后本会话不再弹。
  const showCsat =
    draft.id !== 'new' && !draft.rated && !busy && !draft.candidateOptions &&
    !!lastMsg && lastMsg.role === 'assistant' && !lastMsg.streaming &&
    draft.messages.some((m) => m.role === 'agent')

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
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
          {/* 会话头：统一三入口 SiteNav（智能客服/商城/我的订单），头像接个人中心抽屉；
              顶部导航即返回/去商城的通道，聊天区保持沉浸 */}
          <SiteNav
            avatarOnClick={() => setDrawerOpen(true)}
            avatarChar={customerCtx ? customerCtx.customer.display_name.slice(0, 1) : '演'}
          />

          {/* 消息流 */}
          <div ref={scrollRef} className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-auto px-4 py-3.5">
            {/* 开场白（淘宝式问候气泡，仅首屏）：AI 免责由气泡标签承载；
                快捷入口已常驻输入栏上方，不再放欢迎卡。发首条消息后随消息流自然上移 */}
            {showWelcome && (
              <>
                <MessageBubble
                  message={{
                    id: 'greeting-1',
                    role: 'assistant',
                    content: '您好，我是演示商城的智能客服，很高兴为您服务！',
                    createdAt: 0,
                  }}
                />
                <MessageBubble
                  message={{
                    id: 'greeting-2',
                    role: 'assistant',
                    content: '订单查询、物流进度、退款售后，都可以直接问我～',
                    createdAt: 0,
                  }}
                />
                <div className="self-center rounded-full border border-dashed border-slate-200 bg-slate-50 px-3 py-1 text-[11px] text-slate-500">
                  🧪 演示环境 · 数据为示例
                </div>
              </>
            )}

            {draft.messages.map((m) => (
              <MessageBubble key={m.id} message={m} />
            ))}

            {/* AI 回复未生成（SSE 断流/空回包，error 事件已由 reducer 兜底为系统提示）：
                展示友好提示 + 一键重试，替代生硬的「（空）」占位。人工服务态不显示
                （此时重发会误走坐席消息通道） */}
            {!busy &&
              !['waiting_human', 'agent_offered', 'human_active'].includes(handoffState) &&
              lastMsg && lastMsg.role === 'assistant' && !lastMsg.content && (
              <div className="mx-auto flex w-full max-w-md items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                <span className="flex-1">抱歉，刚才的回复没有生成成功，请重试</span>
                <button
                  type="button"
                  className="shrink-0 rounded-full bg-amber-600 px-2.5 py-1 text-xs text-white hover:bg-amber-700"
                  onClick={retryLast}
                >
                  重试
                </button>
              </div>
            )}

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

          {/* 快捷入口：常驻输入栏上方横滑（淘宝式），发消息后不消失；
              「发订单给客服」chip 待订单选择抽屉（TASK.md M1）落地后加入本行 */}
          <div
            className="flex flex-none items-center gap-2 overflow-x-auto px-3.5 pb-1.5 pt-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
            aria-label="快捷入口"
          >
            <button
              type="button"
              className="relative shrink-0 rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-xs text-indigo-600 hover:bg-indigo-100"
              onClick={() => setOrderSheetOpen(true)}
            >
              📦 发订单给客服
              <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-rose-500" aria-hidden />
            </button>
            {QUICK_ENTRIES.map((q) => (
              <button
                key={q.title}
                type="button"
                className="shrink-0 rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-xs text-indigo-600 hover:bg-indigo-100"
                onClick={() => send(q.text)}
              >
                {q.icon} {q.title}
              </button>
            ))}
            <Link
              href="/mall"
              className="shrink-0 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600 hover:border-indigo-400 hover:text-indigo-600"
            >
              🛍 逛商城
            </Link>
          </div>

          {/* 输入区：转人工为图标入口（有会话且有消息时可用），布局对齐商城客服习惯 */}
          <div className="flex flex-none items-end gap-2 border-t border-slate-200 bg-white px-3.5 pb-3 pt-2">
            <button
              type="button"
              aria-label="发订单给客服"
              title="发订单给客服"
              className="grid h-11 w-11 shrink-0 place-items-center rounded-full border border-indigo-200 bg-indigo-50 text-indigo-600 hover:bg-indigo-100"
              onClick={() => setOrderSheetOpen(true)}
            >
              <Package size={18} aria-hidden />
            </button>
            <button
              type="button"
              aria-label="转人工"
              title="转人工"
              className={`grid h-11 w-11 shrink-0 place-items-center rounded-full border ${
                draft.id !== 'new' && draft.messages.length > 0
                  ? 'border-indigo-200 bg-indigo-50 text-indigo-600 hover:bg-indigo-100'
                  : 'border-slate-200 bg-slate-50 text-slate-400'
              }`}
              disabled={draft.id === 'new' || draft.messages.length === 0}
              onClick={() => void requestHuman()}
            >
              <Headphones size={18} aria-hidden />
            </button>
            <textarea
              className="min-h-11 max-h-32 flex-1 resize-none rounded-3xl border border-slate-200 bg-slate-50 px-4 py-2.5 text-base text-slate-900 outline-none focus:border-indigo-500"
              rows={1}
              placeholder="请描述您的问题…"
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
              aria-label={busy ? '停止生成' : '发送'}
              title={busy ? '停止' : '发送'}
              className={`grid h-11 w-11 shrink-0 place-items-center rounded-full text-white ${
                busy ? 'bg-red-500 hover:bg-red-600' : 'bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300'
              }`}
              disabled={!busy && !input.trim()}
              onClick={() => (busy ? stop() : send(input))}
            >
              {busy ? <Square size={16} /> : <Send size={17} />}
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
      <OrderPickerSheet
        open={orderSheetOpen}
        onClose={() => setOrderSheetOpen(false)}
        onSendOrder={sendOrderRef}
        onSendText={sendSheetText}
        faqEntries={QUICK_ENTRIES}
        preselectOrderNo={preselectOrderNo}
      />

      {drawerOpen && (
        <>
          {/* 抽屉：个人中心 + 历史会话/新咨询（替代顶栏的切换会话下拉） */}
          <div
            className="fixed inset-0 z-40 bg-black/30"
            onClick={() => setDrawerOpen(false)}
            aria-hidden
          />
          <aside
            className="fixed inset-y-0 right-0 z-50 flex w-72 flex-col overflow-auto border-l border-slate-200 bg-white p-4"
            role="dialog"
            aria-label="个人中心"
          >
            <div className="mb-4 flex items-center justify-between">
              <b className="text-sm text-slate-900">个人中心</b>
              <button
                type="button"
                aria-label="关闭抽屉"
                className="grid h-8 w-8 place-items-center rounded-full text-slate-500 hover:bg-slate-100"
                onClick={() => setDrawerOpen(false)}
              >
                ×
              </button>
            </div>
            <div className="mb-4 flex items-center gap-2.5 rounded-xl border border-slate-200 bg-slate-50 p-3">
              <span className="grid h-9 w-9 place-items-center rounded-full bg-indigo-600 text-white">
                {customerCtx ? customerCtx.customer.display_name.slice(0, 1) : '演'}
              </span>
              <div>
                <b className="text-xs text-slate-900">
                  {customerCtx?.customer.display_name ?? '演示用户'}
                </b>
                <div className="text-[11px] text-slate-500">
                  {customerCtx
                    ? customerCtx.customer.level === 'gold'
                      ? '黄金会员'
                      : (customerCtx.customer.level ?? '普通会员')
                    : '普通会员'}
                </div>
              </div>
            </div>
            <nav className="mb-4 flex flex-col gap-1" aria-label="个人中心导航">
              <Link
                href="/my-orders"
                className="rounded-lg px-2.5 py-2 text-sm text-slate-700 hover:bg-slate-50"
                onClick={() => setDrawerOpen(false)}
              >
                📦 我的订单
              </Link>
              <Link
                href="/mall"
                className="rounded-lg px-2.5 py-2 text-sm text-slate-700 hover:bg-slate-50"
                onClick={() => setDrawerOpen(false)}
              >
                🛍 逛商城
              </Link>
              <Link
                href="/settings"
                className="rounded-lg px-2.5 py-2 text-sm text-slate-700 hover:bg-slate-50"
                onClick={() => setDrawerOpen(false)}
              >
                ⚙️ 账号设置
              </Link>
            </nav>
            <b className="mb-1.5 text-xs text-slate-500">历史会话</b>
            <div className="flex flex-col gap-1">
              <button
                type="button"
                className="rounded-lg px-2.5 py-2 text-left text-sm text-indigo-600 hover:bg-indigo-50"
                onClick={() => {
                  startNewSession()
                  setDrawerOpen(false)
                }}
              >
                ＋ 开始新咨询
              </button>
              {convOptions.map((o) => (
                <button
                  key={o.id}
                  type="button"
                  className={`rounded-lg px-2.5 py-2 text-left text-sm hover:bg-slate-50 ${
                    draft.id === o.id ? 'bg-slate-100 text-slate-900' : 'text-slate-700'
                  }`}
                  onClick={() => {
                    selectSession(o.id)
                    setDrawerOpen(false)
                  }}
                >
                  {o.label}
                </button>
              ))}
            </div>
          </aside>
        </>
      )}
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

/** D7：card 消息且首附件为订单引用 → 用户端也渲染订单卡（对齐坐席端 WorkbenchPage 行为） */
function orderRefOf(message: Cs2Message): { order_no: string } | null {
  if (message.contentType !== 'card') return null
  const first = (message.attachments ?? [])[0]
  if (!first || first.type !== 'order_ref') return null
  const orderNo = String(first.order_no ?? '').trim()
  return orderNo ? { order_no: orderNo } : null
}

function MessageBubble({ message }: { message: Cs2Message }) {
  // 空回复且本轮已结束（SSE 断流/空回包）：不渲染空泡和「（空）」占位，
  // 页面的友好重试卡已接管；streaming 中的空占位泡保留（配合 typing 点点）
  if (!message.content && !message.streaming) return null
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
    message.role === 'user'
      ? 'rounded-2xl rounded-br-md bg-indigo-600 px-3 py-2 text-white' // 用户消息：实心品牌色白字（商城客服惯例）
      : message.role === 'agent'
        ? 'rounded-2xl rounded-bl-md bg-emerald-700 px-3 py-2 text-white' // 人工客服消息（#498：600 号白字对比度约 3.9:1 低于 AA，700 约 4.8:1 达标）
        : message.role === 'system'
          ? 'rounded-full border border-dashed border-slate-200 px-3 py-1 text-xs text-slate-500'
          : 'rounded-2xl rounded-bl-md border border-slate-200 bg-white px-3 py-2 text-slate-800'
  // D7：card+order_ref → 订单卡（白底品牌描边，TASK.md 3.3）；否则普通气泡
  const orderRef = orderRefOf(message)

  return (
    <div className={`flex max-w-[78%] flex-col gap-0.5 ${align}`}>
      {message.role !== 'system' && (
        <div className={`flex items-center gap-1.5 text-[11px] text-slate-400 ${message.role === 'user' ? 'justify-end' : ''}`}>
          {message.role === 'assistant' && (
            <span className="grid h-5 w-5 place-items-center rounded-full bg-indigo-600 text-white" aria-hidden>
              <Headphones size={12} />
            </span>
          )}
          {who}
          {message.interrupted && <span className="rounded-full bg-slate-100 px-1.5 text-[10px]">已停止</span>}
        </div>
      )}
      {orderRef ? (
        <div className="rounded-2xl rounded-br-md border border-indigo-300 bg-white px-3 py-2.5 text-slate-800">
          <div className="flex items-center gap-1.5">
            <span className="grid h-5 w-5 place-items-center rounded-full bg-indigo-50 text-[11px]" aria-hidden>📦</span>
            <b className="text-[13px]">订单 {orderRef.order_no}</b>
            <span className="rounded-full bg-indigo-50 px-1.5 py-0.5 text-[10px] text-indigo-600">订单卡</span>
          </div>
          <div className="mt-1 whitespace-pre-wrap text-[12px] leading-relaxed text-slate-600">
            {message.content}
          </div>
        </div>
      ) : (
        <div className={`whitespace-pre-wrap text-[13px] leading-relaxed ${bubble}`}>
          {message.content}
        </div>
      )}
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
