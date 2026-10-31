'use client'

// cs-workstation-v2 P1：统一坐席工作台（/cs2/workspace）。
// 三栏：左（我的会话/待接入双 Tab + 认领）、中（聊天窗：WS 推送 + 断线重连
// 全量对账 + 事件补发 + 降级轮询）、右（会话信息；客户360 P2、AI 助手 P4）。
// 目标态对照 prototypes/agent-workstation.html 的 workspace 视图。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import {
  acceptOffer,
  claimConversation,
  closeConversation,
  createCanned,
  declineOffer,
  getConversationCustomerContext,
  getConversationDetail,
  getConversationEvents,
  getHandoffMessages,
  getHandoffQueue,
  getPendingProposal,
  listMyOffers,
  sendAgentMessage,
  type ConversationCustomerContext,
  type ConversationDetail,
  type HandoffQueueItem,
  type MessageDTO,
  type OfferItem,
  type PendingProposal,
} from './api'
import { useAgentSocket } from './useAgentSocket'
import { AgentStatusBar, NotificationCenter, type NoticeItem } from './AgentTopBar'
import TransferDialog from './TransferDialog'
import CannedReplyPanel from './CannedReplyPanel'
import AssistantPanel from './AssistantPanel'
import type { AssistSnapshot } from './assistTypes'

interface MineEntry {
  conversationId: string
  title: string
  lastPreview: string
  unread: number
  intent: string | null
  handoffState: string
}

const HANDOFF_STATE_ZH: Record<string, string> = {
  ai_active: 'AI 处理中',
  handoff_requested: '请求转人工',
  waiting_human: '待接入',
  agent_offered: '已派单',
  human_active: '人工处理中',
  closed: '已关闭',
  none: 'AI 处理中',
}

function fmtWait(iso: string, now: number): string {
  const s = Math.max(0, Math.floor((now - Date.parse(iso)) / 1000))
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

function msgRole(senderType: string): 'user' | 'agent' | 'bot' | 'sys' {
  if (senderType === 'human_agent') return 'agent'
  if (senderType === 'user') return 'user'
  if (senderType === 'system') return 'sys'
  return 'bot'
}

export default function WorkbenchPage() {
  const [tab, setTab] = useState<'mine' | 'queue'>('mine')
  const [queue, setQueue] = useState<HandoffQueueItem[]>([])
  const [mine, setMine] = useState<MineEntry[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<ConversationDetail | null>(null)
  const [messages, setMessages] = useState<MessageDTO[]>([])
  const [pendingProposal, setPendingProposal] = useState<PendingProposal | null>(null)
  const [customerCtx, setCustomerCtx] = useState<ConversationCustomerContext | null>(null)
  const [handoffState, setHandoffState] = useState<string>('none')
  const [input, setInput] = useState('')
  const [contextTab, setContextTab] = useState<'customer' | 'info' | 'ai'>('info')
  const [notices, setNotices] = useState<NoticeItem[]>([])
  const [offers, setOffers] = useState<OfferItem[]>([])
  const [transferOpen, setTransferOpen] = useState(false)
  const [cannedOpen, setCannedOpen] = useState(false)
  const [assist, setAssist] = useState<AssistSnapshot | null>(null)
  const [userTypingAt, setUserTypingAt] = useState(0)
  const [now, setNow] = useState(() => Date.now())
  const lastIdRef = useRef(0)
  const selectedRef = useRef<string | null>(null)
  const inputRef = useRef<HTMLTextAreaElement | null>(null)

  const refreshQueue = useCallback(() => {
    getHandoffQueue()
      .then((res) => setQueue(res.items ?? []))
      .catch(() => {})
  }, [])

  const appendMessages = useCallback((incoming: MessageDTO[]) => {
    if (!incoming.length) return
    setMessages((prev) => {
      const seen = new Set(prev.map((m) => m.message_id))
      const merged = [...prev]
      for (const m of incoming) {
        if (seen.has(m.message_id)) continue
        seen.add(m.message_id)
        merged.push(m)
      }
      merged.sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at))
      return merged
    })
    const maxId = Math.max(...incoming.map((m) => Number(m.message_id) || 0))
    if (maxId > lastIdRef.current) lastIdRef.current = maxId
  }, [])

  const reloadSelected = useCallback((conversationId: string) => {
    getConversationDetail(conversationId)
      .then((d) => {
        if (selectedRef.current !== conversationId) return
        setDetail(d)
        appendMessages(d.messages ?? [])
      })
      .catch(() => {})
    getHandoffMessages(conversationId, 0)
      .then((res) => {
        if (selectedRef.current !== conversationId) return
        setHandoffState(res.handoff_state ?? 'none')
        appendMessages(res.messages ?? [])
      })
      .catch(() => {})
    // P2：待确认提案快照 + 客户360（均为只读，失败静默）
    getPendingProposal(conversationId)
      .then((snap) => {
        if (selectedRef.current === conversationId) setPendingProposal(snap)
      })
      .catch(() => {})
    getConversationCustomerContext(conversationId)
      .then((ctx) => {
        if (selectedRef.current === conversationId) setCustomerCtx(ctx)
      })
      .catch(() => {})
  }, [appendMessages])

  const pushNotice = useCallback((n: Omit<NoticeItem, 'id' | 'at'>) => {
    setNotices((prev) => [{ ...n, id: crypto.randomUUID(), at: Date.now() }, ...prev].slice(0, 30))
  }, [])

  const refreshOffers = useCallback(() => {
    listMyOffers()
      .then(setOffers)
      .catch(() => {})
  }, [])

  // 挂载即拉队列；之后由轮询与 WS 事件驱动刷新
  useEffect(() => {
    refreshQueue()
    refreshOffers()
  }, [refreshQueue, refreshOffers])

  // 降级轮询：WS 断开时 3s 拉选中会话增量 + 5s 拉队列；连接正常时 20s 对账
  useEffect(() => {
    const t = setInterval(() => {
      setNow(Date.now())
      const cid = selectedRef.current
      if (cid) {
        getHandoffMessages(cid, lastIdRef.current)
          .then((res) => {
            appendMessages(res.messages ?? [])
            setHandoffState(res.handoff_state ?? 'none')
          })
          .catch(() => {})
        getPendingProposal(cid)
          .then((snap) => {
            if (selectedRef.current === cid) setPendingProposal(snap)
          })
          .catch(() => {})
      }
      refreshQueue()
    }, 5_000)
    return () => clearInterval(t)
  }, [appendMessages, refreshQueue])

  const onSocketEvent = useCallback(
    (evt: { type: string; seq: number | null; payload: Record<string, unknown> }) => {
      const cid = String(evt.payload.conversation_id ?? '')
      if (evt.type === 'message.created') {
        const message = evt.payload.message as MessageDTO | undefined
        if (!message) return
        if (cid && cid === selectedRef.current) {
          appendMessages([message])
        } else if (cid) {
          setMine((prev) =>
            prev.map((m) =>
              m.conversationId === cid
                ? { ...m, unread: m.unread + 1, lastPreview: message.content.slice(0, 40) }
                : m,
            ),
          )
        }
        return
      }
      if (evt.type === 'user.typing' && cid === selectedRef.current) {
        setUserTypingAt(Date.now())
        return
      }
      if (evt.type === 'conversation.waiting' || evt.type === 'conversation.reassigned') {
        refreshQueue()
        return
      }
      // P3：定向 offer 到达 → 提示 + 刷新我的 offer 列表
      if (evt.type === 'conversation.offered') {
        pushNotice({
          level: 'info',
          text: '收到新的接入邀请（offer），请尽快接单',
          conversationId: cid,
        })
        refreshOffers()
        return
      }
      if (evt.type === 'conversation.offer_declined' || evt.type === 'conversation.offer_expired') {
        refreshOffers()
        refreshQueue()
        return
      }
      // P3：SLA 分级告警 / 坐席间转接请求 → 通知中心
      if (evt.type === 'sla.warning' || evt.type === 'sla.breach') {
        const level = evt.type === 'sla.breach' ? 'breach' : 'warn'
        pushNotice({
          level,
          text:
            evt.type === 'sla.breach'
              ? `SLA 已超时：会话等待超限，请优先处理${evt.payload.ticket_id ? `（留言工单 ${evt.payload.ticket_id}）` : ''}`
              : 'SLA 预警：会话等待即将超时',
          conversationId: cid,
        })
        return
      }
      if (evt.type === 'conversation.transfer_requested') {
        const from = String(evt.payload.from_agent_id ?? '')
        const to = String(evt.payload.to_agent_id ?? '')
        pushNotice({
          level: 'info',
          text: `转接请求：${from} → ${to}`,
          conversationId: cid,
        })
        return
      }
      if (evt.type === 'conversation.claimed') {
        refreshQueue()
        return
      }
      // P4：AI 辅助推荐（瞬态事件，只对选中会话刷新面板快照）
      if (evt.type === 'assist.suggestion') {
        if (cid && cid === selectedRef.current) {
          setAssist({
            conversationId: cid,
            trigger: String(evt.payload.trigger ?? 'message'),
            intent: (evt.payload.intent as AssistSnapshot['intent']) ?? null,
            emotion: (evt.payload.emotion as AssistSnapshot['emotion']) ?? null,
            sop: (evt.payload.sop as AssistSnapshot['sop']) ?? [],
            suggestions:
              (evt.payload.suggestions as AssistSnapshot['suggestions']) ?? [],
            at: Date.now(),
          })
        }
        return
      }
      if (evt.type === 'conversation.closed') {
        if (cid === selectedRef.current) setHandoffState('closed')
        refreshQueue()
      }
    },
    [appendMessages, refreshQueue, refreshOffers, pushNotice],
  )

  // 重连对账：队列全量 + 选中会话按 seq 游标补发（>lastSeq 的持久化事件）
  const lastSeqRef = useRef(0)
  const onReconnect = useCallback(() => {
    refreshQueue()
    const cid = selectedRef.current
    if (!cid) return
    getConversationEvents(cid, lastSeqRef.current)
      .then((res) => {
        const missed = (res.events ?? [])
          .filter((e) => e.type === 'message.created')
          .map((e) => (e.payload as { message?: MessageDTO }).message)
          .filter((m): m is MessageDTO => !!m)
        appendMessages(missed)
      })
      .catch(() => {})
  }, [appendMessages, refreshQueue])

  const { connected } = useAgentSocket(
    useCallback(
      (evt) => {
        if (typeof evt.seq === 'number' && evt.seq > lastSeqRef.current) {
          lastSeqRef.current = evt.seq
        }
        onSocketEvent(evt)
      },
      [onSocketEvent],
    ),
    { onReconnect },
  )

  const selectConversation = useCallback(
    (conversationId: string, entry?: MineEntry) => {
      // 同步置位：晚到的旧会话响应据此丢弃，防竞态覆盖新选中会话
      selectedRef.current = conversationId
      setSelectedId(conversationId)
      setMessages([])
      setDetail(null)
      lastIdRef.current = 0
      // P4：切换会话不显示上一个会话的 AI 推荐与话术弹层
      setAssist(null)
      setCannedOpen(false)
      if (entry) {
        setMine((prev) => prev.map((m) => (m.conversationId === conversationId ? { ...m, unread: 0 } : m)))
      }
      reloadSelected(conversationId)
    },
    [reloadSelected],
  )

  const claim = useCallback(
    async (item: HandoffQueueItem) => {
      try {
        await claimConversation(item.conversation_id)
        setQueue((prev) => prev.filter((q) => q.conversation_id !== item.conversation_id))
        setMine((prev) =>
          prev.some((m) => m.conversationId === item.conversation_id)
            ? prev
            : [
                {
                  conversationId: item.conversation_id,
                  title: `用户 ${item.user_id.slice(0, 8)}`,
                  lastPreview: item.last_message_preview ?? '',
                  unread: 0,
                  intent: null,
                  handoffState: 'human_active',
                },
                ...prev,
              ],
        )
        setTab('mine')
        selectConversation(item.conversation_id)
      } catch {
        /* 409 已被他人认领等：下一轮队列刷新自然收敛 */
      }
    },
    [selectConversation],
  )

  const close = useCallback(async () => {
    const cid = selectedRef.current
    if (!cid) return
    try {
      await closeConversation(cid)
      setHandoffState('closed')
      setMine((prev) => prev.map((m) => (m.conversationId === cid ? { ...m, handoffState: 'closed' } : m)))
      refreshQueue()
    } catch {
      /* 关闭失败保持原状态 */
    }
  }, [refreshQueue])

  const doAcceptOffer = useCallback(
    async (offer: OfferItem) => {
      try {
        await acceptOffer(offer.handoff_id, offer.assignment_version)
        setOffers((prev) => prev.filter((o) => o.handoff_id !== offer.handoff_id))
        setTab('mine')
        selectConversation(offer.conversation_id)
        refreshQueue()
      } catch {
        pushNotice({ level: 'warn', text: '接单失败：offer 可能已超时或被处理', conversationId: offer.conversation_id })
        refreshOffers()
      }
    },
    [pushNotice, refreshOffers, refreshQueue, selectConversation],
  )

  const doDeclineOffer = useCallback(
    async (offer: OfferItem) => {
      try {
        await declineOffer(offer.handoff_id, offer.assignment_version)
      } catch {
        /* 拒单失败静默：offer 超时回收会自然收敛 */
      }
      setOffers((prev) => prev.filter((o) => o.handoff_id !== offer.handoff_id))
      refreshQueue()
    },
    [refreshQueue],
  )

  const send = useCallback(async () => {
    const cid = selectedRef.current
    const content = input.trim()
    if (!cid || !content || handoffState === 'closed') return
    setInput('')
    try {
      const res = await sendAgentMessage(cid, content)
      appendMessages([
        {
          message_id: res.message_id,
          sender_type: res.sender_type ?? 'human_agent',
          content: res.content ?? content,
          content_type: 'text',
          created_at: res.created_at ?? new Date().toISOString(),
        },
      ])
    } catch {
      /* 发送失败：输入不回填，避免重复点击重复发送 */
    }
  }, [appendMessages, handoffState, input])

  const lastIntent = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].intent_name) return messages[i].intent_name as string
    }
    return null
  }, [messages])

  // ── P4：快捷回复 / AI 助手动作 ──────────────────────────
  const insertIntoInput = useCallback((content: string) => {
    setInput((prev) => (prev.trim() ? `${prev.replace(/\s*$/, '')}\n${content}` : content))
    inputRef.current?.focus()
  }, [])

  const adoptSuggestion = useCallback((text: string) => {
    setInput(text)
    inputRef.current?.focus()
  }, [])

  const quoteSuggestion = useCallback((text: string, source: string) => {
    const quote = `「${text}」—— 来源：${source}`
    setInput((prev) => (prev.trim() ? `${prev.replace(/\s*$/, '')}\n${quote}` : quote))
    inputRef.current?.focus()
  }, [])

  const saveSuggestionAsCanned = useCallback(
    (text: string, _source: string) => {
      const title = `AI·${text.replace(/\s+/g, '').slice(0, 6)}`
      createCanned({ title, content: text, category: 'AI', scope: 'personal' })
        .then(() => {
          pushNotice({ level: 'info', text: `已存为个人话术「${title}」，快捷回复中可用` })
        })
        .catch(() => {
          pushNotice({ level: 'warn', text: '存为话术失败，请重试' })
        })
    },
    [pushNotice],
  )

  const closed = handoffState === 'closed'
  const showUserTyping = Date.now() - userTypingAt < 3_000

  return (
    <div className="grid h-full min-h-0 grid-cols-[260px_minmax(0,1fr)_300px]">
      {/* 左栏 */}
      <div className="flex min-h-0 flex-col border-r border-slate-200 bg-white">
        {/* P3：我的待接单（定向 offer）——接单即进入我的会话 */}
        {offers.length > 0 && (
          <div className="flex-none border-b border-amber-200 bg-amber-50 p-2">
            {offers.map((o) => (
              <div key={o.handoff_id} className="mb-1.5 rounded-lg border border-amber-300 bg-white p-2 text-xs last:mb-0">
                <b className="text-slate-900">新接入邀请</b>
                <span className="ml-1.5 text-slate-500">
                  会话 {o.conversation_id.slice(0, 10)}…
                  {o.offer_expires_at && ` · ${Math.max(0, Math.ceil((Date.parse(o.offer_expires_at) - now) / 1000))}s 后过期`}
                </span>
                <div className="mt-1.5 flex gap-2">
                  <button
                    type="button"
                    className="rounded-lg bg-indigo-600 px-2.5 py-1 text-[11px] text-white hover:opacity-90"
                    onClick={() => void doAcceptOffer(o)}
                  >
                    接单
                  </button>
                  <button
                    type="button"
                    className="rounded-lg border border-slate-200 px-2.5 py-1 text-[11px] text-slate-600 hover:bg-slate-50"
                    onClick={() => void doDeclineOffer(o)}
                  >
                    拒单
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
        <div className="flex flex-none border-b border-slate-200" role="tablist" aria-label="会话列表">
          {(
            [
              ['mine', '我的会话', mine.length],
              ['queue', '待接入', queue.length],
            ] as const
          ).map(([key, label, count]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={tab === key}
              className={`flex-1 border-b-2 px-2 py-2.5 text-[13px] ${tab === key ? 'border-indigo-600 text-indigo-600' : 'border-transparent text-slate-500'}`}
              onClick={() => setTab(key)}
            >
              {label} <b>{count}</b>
            </button>
          ))}
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-1.5">
          {tab === 'mine' &&
            (mine.length === 0 ? (
              <p className="p-4 text-xs text-slate-400">暂无我的会话 —— 从「待接入」认领后进入这里。</p>
            ) : (
              mine.map((m) => (
                <ConversationRow
                  key={m.conversationId}
                  title={m.title}
                  badge={m.intent}
                  time=""
                  preview={m.lastPreview}
                  unread={m.unread}
                  selected={selectedId === m.conversationId}
                  onClick={() => selectConversation(m.conversationId, m)}
                />
              ))
            ))}
          {tab === 'queue' &&
            (queue.length === 0 ? (
              <p className="p-4 text-xs text-slate-400">队列为空。</p>
            ) : (
              queue.map((q) => (
                <ConversationRow
                  key={q.conversation_id}
                  title={`用户 ${q.user_id.slice(0, 8)}`}
                  badge={q.trigger_type === 'complaint_escalation' ? '投诉升级' : null}
                  time={`等待 ${fmtWait(q.updated_at, now)}`}
                  preview={q.last_message_preview ?? ''}
                  unread={0}
                  selected={false}
                  claimable
                  onClaim={() => void claim(q)}
                  onClick={() => {}}
                />
              ))
            ))}
        </div>
        <div className="flex flex-none items-center gap-2 px-3 py-2 text-[11px] text-slate-400">
          <span className={`h-1.5 w-1.5 rounded-full ${connected ? 'bg-emerald-500' : 'bg-amber-400'}`} aria-hidden />
          {connected ? '实时推送已连接' : '连接中断 · 轮询降级中'}
          <span className="ml-auto" />
          <AgentStatusBar onNotice={pushNotice} />
          <NotificationCenter
            notices={notices}
            onLocate={(cid) => {
              setTab('mine')
              selectConversation(cid)
            }}
          />
        </div>
      </div>

      {/* 中栏 */}
      <div className="flex min-h-0 min-w-0 flex-col">
        {selectedId ? (
          <>
            <div className="flex h-12 flex-none items-center gap-2 border-b border-slate-200 bg-white px-3.5">
              <b className="text-sm text-slate-900">{detail?.summary || `会话 ${selectedId.slice(0, 8)}`}</b>
              {lastIntent && (
                <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] text-indigo-600">{lastIntent}</span>
              )}
              <div className="ml-auto flex items-center gap-2.5">
                <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] text-emerald-600">
                  {HANDOFF_STATE_ZH[handoffState] ?? handoffState}
                </span>
              </div>
            </div>
            <div className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-auto px-4 py-3.5">
              {messages.map((m) => (
                <AgentBubble key={m.message_id} message={m} />
              ))}
              {/* P2：用户待确认提案卡（等待确认 → 执行中/成功由后续事件联动） */}
              {pendingProposal && <ProposalCard proposal={pendingProposal} />}
              {showUserTyping && (
                <div className="self-start px-1 text-xs text-slate-400">
                  对方正在输入
                  <span className="ml-1 inline-flex gap-0.5">
                    <i className="h-1 w-1 animate-bounce rounded-full bg-slate-400" />
                    <i className="h-1 w-1 animate-bounce rounded-full bg-slate-400 [animation-delay:150ms]" />
                    <i className="h-1 w-1 animate-bounce rounded-full bg-slate-400 [animation-delay:300ms]" />
                  </span>
                </div>
              )}
            </div>
            <div className="relative flex-none border-t border-slate-200 bg-white px-3 pb-3 pt-2">
              <div className="mb-2 flex gap-1.5">
                <button
                  type="button"
                  aria-expanded={cannedOpen}
                  className={`rounded-lg border px-2.5 py-1 text-xs ${cannedOpen ? 'border-indigo-300 bg-indigo-50 text-indigo-600' : 'border-slate-200 text-slate-600 hover:bg-slate-50'}`}
                  onClick={() => setCannedOpen((v) => !v)}
                >
                  快捷回复
                </button>
                <button
                  type="button"
                  className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
                  onClick={() => setContextTab('ai')}
                  title="在右栏查看 AI 推荐"
                >
                  AI 建议
                </button>
                {!closed && (
                  <button
                    type="button"
                    className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
                    onClick={() => setTransferOpen(true)}
                  >
                    转接
                  </button>
                )}
                <button
                  type="button"
                  className="ml-auto rounded-lg border border-slate-200 px-2.5 py-1 text-xs text-slate-500 hover:bg-slate-50 disabled:opacity-45"
                  disabled={closed}
                  onClick={() => void close()}
                >
                  关闭会话
                </button>
              </div>
              <CannedReplyPanel
                open={cannedOpen}
                onClose={() => setCannedOpen(false)}
                onInsert={insertIntoInput}
              />
              <div className="flex items-end gap-2">
                <textarea
                  ref={inputRef}
                  className="min-h-14 flex-1 resize-none rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-base text-slate-900 outline-none focus:border-indigo-500 disabled:opacity-60"
                  rows={2}
                  placeholder={closed ? '会话已关闭（用户侧将收到满意度评价邀请）' : '输入回复…（Enter 发送，Shift+Enter 换行）'}
                  aria-label="回复输入框"
                  value={input}
                  disabled={closed}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      void send()
                    }
                  }}
                />
                <button
                  type="button"
                  className="min-h-14 rounded-xl bg-indigo-600 px-4 text-sm text-white hover:opacity-90 disabled:opacity-45"
                  disabled={closed}
                  onClick={() => void send()}
                >
                  发送
                </button>
              </div>
            </div>
          </>
        ) : (
          <div className="grid place-items-center text-sm text-slate-400">← 从左侧选择会话开始处理</div>
        )}
      </div>

      {/* 右栏 */}
      <div className="flex min-h-0 flex-col border-l border-slate-200 bg-white">
        <div className="flex flex-none border-b border-slate-200" role="tablist" aria-label="上下文面板">
          {(
            [
              ['customer', '客户360', 'P2 落地'],
              ['info', '会话信息', undefined],
              ['ai', 'AI助手', 'P4 落地'],
            ] as const
          ).map(([key, label, hint]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={contextTab === key}
              className={`flex-1 border-b-2 px-1 py-2.5 text-xs ${contextTab === key ? 'border-indigo-600 text-indigo-600' : 'border-transparent text-slate-500'}`}
              onClick={() => setContextTab(key)}
              title={hint}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-3">
          {contextTab === 'info' ? (
            <ContextInfo
              detail={detail}
              handoffState={handoffState}
              intent={lastIntent}
              pendingProposal={pendingProposal}
            />
          ) : contextTab === 'customer' ? (
            <Customer360 ctx={customerCtx} />
          ) : (
            <AssistantPanel
              assist={assist}
              selected={!!selectedId && !closed}
              onAdopt={adoptSuggestion}
              onQuote={quoteSuggestion}
              onSaveCanned={saveSuggestionAsCanned}
            />
          )}
        </div>
      </div>

      {/* P3：转接弹窗 */}
      {transferOpen && selectedId && (
        <TransferDialog
          conversationId={selectedId}
          onClose={() => setTransferOpen(false)}
          onDone={(result) => {
            setTransferOpen(false)
            pushNotice({
              level: 'info',
              text: `已发起转接 → ${result.to_agent_id}（对方接单前可从队列重新认领）`,
              conversationId: result.conversation_id,
            })
            setHandoffState('agent_offered')
            refreshQueue()
          }}
        />
      )}
    </div>
  )
}

function ContextInfo({
  detail,
  handoffState,
  intent,
  pendingProposal,
}: {
  detail: ConversationDetail | null
  handoffState: string
  intent: string | null
  pendingProposal: PendingProposal | null
}) {
  if (!detail) {
    return <p className="p-2 text-xs text-slate-400">选择会话后展示会话信息。</p>
  }
  return (
    <div className="flex flex-col gap-4">
      <section>
        <h4 className="mb-2 text-xs text-slate-500">会话状态</h4>
        <Kv k="意图" v={intent ?? '—'} />
        <Kv k="状态" v={HANDOFF_STATE_ZH[handoffState] ?? handoffState} />
        <Kv k="处理模式" v={detail.handling_mode} />
        <Kv k="优先级" v={detail.priority} />
      </section>
      <section>
        <h4 className="mb-2 text-xs text-slate-500">会话上下文</h4>
        <Kv k="订单上下文" v={pendingProposal ? `${pendingProposal.target_type}:${pendingProposal.target_id}` : '—'} />
        <Kv
          k="用户待确认提案"
          v={pendingProposal ? `${pendingProposal.summary.slice(0, 22)}（v${pendingProposal.version}）` : '—'}
        />
      </section>
      <section>
        <h4 className="mb-2 text-xs text-slate-500">AI 阶段摘要（接管上下文）</h4>
        <p className="rounded-lg bg-slate-50 p-2.5 text-xs leading-relaxed text-slate-600">
          {detail.context_summary || '—'}
        </p>
      </section>
      <section>
        <h4 className="mb-2 text-xs text-slate-500">链路 Trace</h4>
        <Kv k="Trace 数" v={String(detail.trace_count)} />
        {detail.last_trace_id ? (
          <Link
            href={`/observability/traces/${detail.last_trace_id}`}
            className="inline-block rounded-lg border border-slate-200 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
          >
            打开 Trace 详情
          </Link>
        ) : (
          <Kv k="最近 Trace" v="—" />
        )}
      </section>
    </div>
  )
}

const PROPOSAL_STATE_BADGE: Record<string, string> = {
  pending: 'bg-amber-50 text-amber-600',
  paused_handoff: 'bg-blue-50 text-blue-600',
  expired: 'bg-red-50 text-red-500',
}
const PROPOSAL_STATE_ZH: Record<string, string> = {
  pending: '等待用户确认',
  paused_handoff: '人工接管中（暂停）',
  expired: '已过期',
}

function ProposalCard({ proposal }: { proposal: PendingProposal }) {
  const badge = PROPOSAL_STATE_BADGE[proposal.state] ?? 'bg-slate-100 text-slate-500'
  const stateZh = PROPOSAL_STATE_ZH[proposal.state] ?? proposal.state
  return (
    <div className="mx-auto w-full max-w-sm rounded-xl border border-slate-200 bg-white p-3 text-xs">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <b className="text-slate-900">用户待确认提案</b>
        <span className={`rounded-full px-2 py-0.5 text-[10.5px] ${badge}`}>{stateZh}</span>
        <span className="ml-auto text-[10.5px] text-slate-400">v{proposal.version}</span>
      </div>
      <Kv k="内容" v={proposal.summary} />
      <Kv k="对象" v={`${proposal.target_type} · ${proposal.target_id}`} />
      {proposal.reason && <Kv k="原因" v={proposal.reason} />}
      {proposal.expires_at && (
        <Kv k="有效期至" v={proposal.expires_at.slice(5, 16).replace('T', ' ')} />
      )}
      <p className="mt-1.5 text-[10.5px] leading-relaxed text-slate-400">
        执行以用户确认为准（幂等）；接手会话后可结合提案内容回应用户。
      </p>
    </div>
  )
}

function Customer360({ ctx }: { ctx: ConversationCustomerContext | null }) {
  if (!ctx) {
    return <p className="p-2 text-xs text-slate-400">选择会话后展示客户 360 视图。</p>
  }
  return (
    <div className="flex flex-col gap-4">
      <section>
        <h4 className="mb-2 text-xs text-slate-500">会员信息</h4>
        <div className="mb-1.5 flex items-center gap-2">
          <span className="grid h-8 w-8 place-items-center rounded-full bg-indigo-50 text-sm text-indigo-600">
            {ctx.customer.display_name.slice(0, 1)}
          </span>
          <div>
            <b className="text-xs text-slate-900">{ctx.customer.display_name}</b>
            <div className="text-[11px] text-slate-500">
              {ctx.customer.level ?? '普通会员'}
              {ctx.customer.register_time ? ` · 注册 ${ctx.customer.register_time.slice(0, 10)}` : ''}
            </div>
          </div>
        </div>
        <Kv k="账户余额（只读）" v={ctx.balance ? `¥${ctx.balance.balance.toFixed(2)}` : '—'} />
      </section>
      <section>
        <h4 className="mb-2 text-xs text-slate-500">近期订单</h4>
        {ctx.orders.length === 0 && <p className="text-[11px] text-slate-400">暂无订单。</p>}
        {ctx.orders.map((o) => (
          <div key={o.order_no} className="mb-1.5 flex items-center gap-2 rounded-lg border border-slate-100 px-2.5 py-2 text-xs">
            <div className="min-w-0 flex-1">
              <b className="block text-slate-900">{o.order_no}</b>
              <span className="block truncate text-[11px] text-slate-500">{o.product_summary}</span>
            </div>
            <div className="text-right">
              <div className="text-slate-700">
                {o.total_amount != null ? `¥${o.total_amount.toFixed(2)}` : ''}
              </div>
              <span className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[10.5px] text-slate-500">
                {o.status}
              </span>
            </div>
          </div>
        ))}
      </section>
      <section>
        <h4 className="mb-2 text-xs text-slate-500">标签</h4>
        <p className="text-[11px] text-slate-400">标签体系随 P7 知识/画像落地。</p>
      </section>
    </div>
  )
}

function Kv({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-center justify-between gap-2.5 border-b border-dashed border-slate-100 py-1.5 text-xs">
      <b className="flex-none font-normal text-slate-500">{k}</b>
      <span className="min-w-0 truncate text-right text-slate-800">{v}</span>
    </div>
  )
}

function ConversationRow({
  title,
  badge,
  time,
  preview,
  unread,
  selected,
  claimable = false,
  onClick,
  onClaim,
}: {
  title: string
  badge: string | null
  time: string
  preview: string
  unread: number
  selected: boolean
  claimable?: boolean
  onClick: () => void
  onClaim?: () => void
}) {
  return (
    <div
      className={`mb-0.5 flex cursor-pointer flex-col gap-1 rounded-lg border p-2.5 ${selected ? 'border-indigo-200 bg-indigo-50' : 'border-transparent hover:bg-slate-50'}`}
      onClick={onClick}
    >
      <div className="flex min-w-0 items-center gap-1.5">
        <b className="whitespace-nowrap text-xs text-slate-900">{title}</b>
        {badge && (
          <span className="rounded-full bg-indigo-50 px-2 py-px text-[10.5px] text-indigo-600">{badge}</span>
        )}
        <span className="ml-auto whitespace-nowrap text-[11px] text-slate-400">{time}</span>
      </div>
      <div className="flex min-w-0 items-center gap-1.5 text-[11.5px] text-slate-500">
        <span className="truncate">{preview || '—'}</span>
        {unread > 0 && (
          <span className="ml-auto grid h-4 w-4 flex-none place-items-center rounded-full bg-red-500 text-[10px] text-white">
            {unread}
          </span>
        )}
        {claimable && onClaim && (
          <button
            type="button"
            className="ml-auto rounded-lg bg-indigo-600 px-2.5 py-1 text-xs text-white hover:opacity-90"
            onClick={(e) => {
              e.stopPropagation()
              onClaim()
            }}
          >
            认领
          </button>
        )}
      </div>
    </div>
  )
}

function AgentBubble({ message }: { message: MessageDTO }) {
  const role = msgRole(message.sender_type)
  const align =
    role === 'agent' ? 'self-end' : role === 'sys' ? 'self-center max-w-[88%]' : 'self-start'
  const bubble =
    role === 'agent'
      ? 'rounded-xl bg-indigo-50 px-3 py-2'
      : role === 'bot'
        ? 'rounded-xl border border-slate-100 bg-slate-50 px-3 py-2'
        : role === 'sys'
          ? 'rounded-full border border-dashed border-slate-200 px-3 py-1 text-xs text-slate-500'
          : 'rounded-xl border border-slate-200 bg-white px-3 py-2'
  const who =
    role === 'user' ? '访客' : role === 'agent' ? '坐席 · 我' : role === 'bot' ? '机器人' : '系统'
  return (
    <div className={`flex max-w-[74%] flex-col gap-0.5 ${align}`}>
      {role !== 'sys' && (
        <div className={`flex items-center gap-1.5 text-[11px] text-slate-400 ${role === 'agent' ? 'justify-end' : ''}`}>
          {who}
          {message.intent_name && (
            <span className="rounded-full bg-slate-100 px-1.5 text-[10px]">{message.intent_name}</span>
          )}
        </div>
      )}
      <div className={`whitespace-pre-wrap text-[13px] leading-relaxed text-slate-800 ${bubble}`}>
        {message.content}
      </div>
    </div>
  )
}
