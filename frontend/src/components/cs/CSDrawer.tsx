'use client'

/**
 * CSDrawer — 用户端客服会话组件
 *
 * 复用现有 CS 组件树（CSWelcome/CSMessageList/CSStatusBar/CSInput/CSHandoffCard）
 * 与 useCSChat 流式 hook，在独立客服页展示会话；保留抽屉模式供旧入口兼容。
 * 组件装配方式与管理端 frontend-admin/src/app/cs/page.tsx 保持一致。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Headphones, House, Plus, UserRound, X } from 'lucide-react'
import MobileAssistantDrawer from '@/components/layout/MobileAssistantDrawer'
import { useCSChatStore } from '@/store/csChat'
import { useCSChat } from '@/hooks/useCSChat'
import { useCSHandoffSync } from '@/hooks/useCSHandoffSync'
import {
  listMyConversations,
  listMyTickets,
  getMyPendingAction,
  type MyTicket,
  type PendingActionSnapshot,
  confirmAction,
  notifyUserTyping,
  requestHandoff,
} from '@/api/cs'
import { ApiError } from '@/api/client'
import CSWelcome from '@/components/cs/CSWelcome'
import CSMessageList from '@/components/cs/CSMessageList'
import CSInput from '@/components/cs/CSInput'
import CSStatusBar from '@/components/cs/CSStatusBar'
import CSHandoffCard from '@/components/cs/CSHandoffCard'
import CSConfirmCard from '@/components/cs/CSConfirmCard'
import CSSatisfactionCard from '@/components/cs/CSSatisfactionCard'
import MobileAssistantDrawer from '@/components/layout/MobileAssistantDrawer'

interface CSDrawerProps {
  open: boolean
  onClose: () => void
  /** true 时作为独立客服页内的聊天主栏展示。 */
  standalone?: boolean
  onOpenCustomerContext?: () => void
}

export default function CSDrawer({
  open, onClose, standalone = false, onOpenCustomerContext,
}: CSDrawerProps) {
  const { startStream, stopStream } = useCSChat()

  const currentId = useCSChatStore((s) => s.currentId)
  const messages = useCSChatStore((s) =>
    s.sessions.find((sess) => sess.id === s.currentId)?.messages ?? []
  )
  const sessions = useCSChatStore((s) => s.sessions)
  const isLoading = useCSChatStore((s) => s.isLoading)
  const error = useCSChatStore((s) => s.error)
  const currentStatus = useCSChatStore((s) => s.currentStatus)
  const intentDetected = useCSChatStore((s) => s.intentDetected)
  const handoffState = useCSChatStore((s) => s.handoffState)
  const currentNode = useCSChatStore((s) => s.currentNode)
  const csTimeline = useCSChatStore((s) => s.csTimeline)
  const setError = useCSChatStore((s) => s.setError)
  const setHandoffState = useCSChatStore((s) => s.setHandoffState)
  const newSession = useCSChatStore((s) => s.newSession)
  const switchSession = useCSChatStore((s) => s.switchSession)
  const pendingProposal = useCSChatStore((s) => s.pendingBySession[s.currentId] ?? null)
  const candidateOptions = useCSChatStore((s) => s.candidateOptions)
  const handoffMeta = useCSChatStore((s) => s.handoffMeta)
  // A 案倒计时（2026-10-08）：waiting_human 时按 total_deadline_at 每秒刷新剩余秒数
  const [waitRemaining, setWaitRemaining] = useState<number | null>(null)
  const [tickets, setTickets] = useState<MyTicket[]>([])
  const [ticketsOpen, setTicketsOpen] = useState(false)
  useEffect(() => {
    if (handoffState !== 'waiting' || !handoffMeta?.total_deadline_at) {
      setWaitRemaining(null)
      return
    }
    const deadline = Date.parse(handoffMeta.total_deadline_at)
    if (Number.isNaN(deadline)) {
      setWaitRemaining(null)
      return
    }
    const tick = () => setWaitRemaining(Math.max(0, Math.ceil((deadline - Date.now()) / 1000)))
    tick()
    const t = setInterval(tick, 1000)
    return () => clearInterval(t)
  }, [handoffState, handoffMeta?.total_deadline_at])
  const agentTyping = useCSChatStore((s) => s.agentTyping)
  const addMessage = useCSChatStore((s) => s.addMessage)
  const setPendingAction = useCSChatStore((s) => s.setPendingAction)

  const [handoffPending, setHandoffPending] = useState(false)
  const [confirmBusy, setConfirmBusy] = useState(false)
  const confirmBusyRef = useRef(false)
  const handoffRequestRef = useRef(false)
  const handoffKeyRef = useRef<{ conversationId: string; key: string } | null>(null)
  const handoffOwnsConversation =
    handoffState === 'requested' || handoffState === 'waiting' || handoffState === 'active'

  // 主助手引导卡带来的预填问题（HandoffCard 写 sessionStorage + 派发事件）。
  // 独立客服页打开时读取并清除，nonce 触发 CSInput 覆盖。
  const [prefillDraft, setPrefillDraft] = useState<{ text: string; nonce: number } | null>(null)
  useEffect(() => {
    if (!open) return
    try {
      const prefill = sessionStorage.getItem('cs_handoff_prefill')
      if (prefill) {
        sessionStorage.removeItem('cs_handoff_prefill')
        setPrefillDraft((prev) => ({ text: prefill, nonce: (prev?.nonce ?? 0) + 1 }))
      }
    } catch {
      /* sessionStorage 不可用时跳过预填 */
    }
  }, [open])

  // 确认卡片：本地同步闸门防双击，服务端仍以 PG 原子认领为最终幂等门。
  // 409 后重读权威快照并说明状态变化，不依据旧客户端状态自行清卡。
  const handleConfirmAction = useCallback(
    async (decision: 'confirm' | 'cancel') => {
      const pending = useCSChatStore.getState().pendingBySession[currentId]
      if (
        confirmBusyRef.current
        || !pending
        || pending.state !== 'pending'
        || handoffOwnsConversation
      ) return
      confirmBusyRef.current = true
      setConfirmBusy(true)
      try {
        const resp = await confirmAction(currentId, pending, decision, crypto.randomUUID())
        addMessage('assistant', resp.answer, currentId)
        setPendingAction(currentId, null)
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          try {
            const latest = await getMyPendingAction(currentId)
            setPendingAction(currentId, latest)
            addMessage(
              'assistant',
              latest
                ? latest.state === 'expired'
                  ? '这项操作的确认已过期，请重新发起后再办理。'
                  : latest.state === 'paused_handoff'
                    ? '人工客服已接管会话，这项操作暂时暂停。'
                    : '这项操作的确认信息已更新，请检查最新内容后再选择。'
                : '这项操作已完成、取消或过期，当前没有待确认事项。',
              currentId,
            )
          } catch {
            setError('确认状态已变化，但最新状态暂不可用。请稍后重试或重新打开会话。')
          }
          return
        }
        setError(`操作处理失败：${err instanceof Error ? err.message : '未知错误'}`)
      } finally {
        confirmBusyRef.current = false
        setConfirmBusy(false)
      }
    },
    [currentId, addMessage, handoffOwnsConversation, setError, setPendingAction],
  )

  // 人工介入同步：坐席消息轮询入列 + 转接状态卡片（抽屉打开期间生效）
  useCSHandoffSync(currentId, open)

  // csChat store 纯内存，刷新即失——打开抽屉时从 CS 域恢复最近会话
  // （GET /cs/conversations/my 按登录身份过滤；静默失败不打扰， guest 401 跳过）。
  // STOP CS-A P0-3/F5：每次打开都重新拉取（不再 hydratedRef 一次性）——
  // 关抽屉期间后端继续执行的轮次，重开抽屉时由服务端补齐（store 侧
  // 只并入不回退，数据不丢）。
  useEffect(() => {
    if (!open) return
    listMyConversations(10).then((items) => {
      if (items.length > 0) useCSChatStore.getState().hydrateFromServer(items)
    })
    // 历史工单（2026-10-08 拍板：客服域要有工单真实记录展示）；失败静默为空
    listMyTickets(8).then(setTickets)
  }, [open])

  // 打开抽屉或切换会话时，从认证后的 PostgreSQL 接口恢复本会话待确认状态。
  useEffect(() => {
    if (!open || !currentId) return
    let active = true
    getMyPendingAction(currentId)
      .then((pending) => {
        if (active) setPendingAction(currentId, pending)
      })
      .catch(() => {
        // 快照读取失败不清本地已有卡片；下一次打开/切换会话会再尝试。
      })
    return () => { active = false }
  }, [open, currentId, setPendingAction])

  // 满意度评价：会话有回复且非流式中显示；切换会话时重置
  const [showSatisfaction, setShowSatisfaction] = useState(true)
  useEffect(() => {
    setShowSatisfaction(true)
  }, [currentId])
  const lastRole = messages.length > 0 ? messages[messages.length - 1].role : null
  const showRatingCard = messages.length > 0 && !isLoading && showSatisfaction
    && !pendingProposal && (lastRole === 'assistant' || lastRole === 'agent')

  const handleSend = useCallback(
    (text: string) => {
      if (handoffOwnsConversation) return
      setError(null)
      startStream(text, currentId)
    },
    [currentId, handoffOwnsConversation, startStream, setError]
  )

  // P4：显式按钮直连入池接口，不把“转人工”伪装成一条自然语言问题。
  // key 在同一会话内保持稳定：即使请求超时但数据库已经提交，重试仍会
  // 命中同一幂等语义；切换会话后再生成新 key。
  const handleRequestHandoff = useCallback(async () => {
    if (
      !currentId
      || isLoading
      || handoffState !== 'none'
      || handoffRequestRef.current
    ) return

    handoffRequestRef.current = true
    setHandoffPending(true)
    setError(null)
    setHandoffState('requested')

    let key = handoffKeyRef.current
    if (!key || key.conversationId !== currentId) {
      key = { conversationId: currentId, key: crypto.randomUUID() }
      handoffKeyRef.current = key
    }

    try {
      const response = await requestHandoff(currentId, key.key)
      const state = response.handoff_state === 'human_active'
        ? 'active'
        : response.handoff_state === 'closed'
          ? 'closed'
          : 'waiting'
      setHandoffState(state)
    } catch (err) {
      setHandoffState('none')
      setError(`转接人工失败：${err instanceof Error ? err.message : '请稍后重试'}`)
    } finally {
      handoffRequestRef.current = false
      setHandoffPending(false)
    }
  }, [currentId, handoffState, isLoading, setError, setHandoffState])

  // 用户「输入中」上行：2s 节流（服务端 TTL 5s，持续输入自然续期）。
  // 仅转人工后（handoffState='active'）上报——AI 阶段无坐席在线，无需打扰。
  const lastTypingSentRef = useRef(0)
  const handleUserTyping = useCallback(() => {
    if (handoffState !== 'active' || !currentId) return
    const now = Date.now()
    if (now - lastTypingSentRef.current < 2000) return
    lastTypingSentRef.current = now
    void notifyUserTyping(currentId)
  }, [handoffState, currentId])

  const hasMessages = messages.length > 0

  return (
    <>
      {/* 遮罩：点击关闭 */}
      {open && !standalone && (
        <div
          className="fixed inset-0 z-40 bg-black/20 transition-opacity"
          onClick={onClose}
        />
      )}

      {/* 客服对话面板：独立页整屏展示，用户端入口作为右侧抽屉
          宽度：桌面固定 440px，窄屏撑满可用宽度（原 max-w-[92vw] 会留出 8vw 的无用边缝，
          遮罩下露出主界面且在 1024px 级屏幕上浪费近 80px 内容宽度） */}
      <div className={standalone
        ? 'relative z-10 flex h-[100dvh] w-full min-w-0 flex-col border-x border-border-subtle bg-white shadow-xl'
        : `fixed right-0 top-0 z-50 h-full w-[440px] max-sm:w-full
          flex flex-col border-l border-border-subtle bg-white shadow-2xl
          transition-transform duration-300 ease-out
          ${open ? 'translate-x-0' : 'translate-x-full'}`}>
        {/* Header */}
        <div className="shrink-0 flex items-center gap-2.5 px-4 py-3 border-b border-border-subtle bg-white">
          <div className="w-8 h-8 rounded-lg bg-accent/10 flex items-center justify-center shrink-0">
            <Headphones size={17} className="text-accent" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold text-text-primary truncate">智能客服</h2>
            {sessions.length > 1 ? (
              <select
                aria-label="切换客服会话"
                value={currentId}
                onChange={(event) => switchSession(event.target.value)}
                className="block max-w-full bg-transparent text-[10px] text-text-muted
                  truncate outline-none cursor-pointer"
              >
                {sessions.map((session) => (
                  <option key={session.id} value={session.id}>
                    {session.title || '客服会话'}
                  </option>
                ))}
              </select>
            ) : (
              <p className="text-[10px] text-text-muted truncate">AI 驱动的客户服务中心</p>
            )}
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {standalone && <MobileAssistantDrawer />}
            {standalone && onOpenCustomerContext && (
              <button
                type="button"
                onClick={onOpenCustomerContext}
                title="查看客户资料和订单"
                aria-label="查看客户资料和订单"
                className="flex lg:hidden items-center gap-1 px-2 h-7 rounded-lg
                  border border-border-subtle text-[11px] text-text-secondary
                  hover:text-text-primary hover:border-accent/40 transition-colors"
              >
                <UserRound size={13} />
                <span>资料</span>
              </button>
            )}
            {hasMessages && handoffState === 'none' && (
              <button
                onClick={handleRequestHandoff}
                disabled={isLoading || handoffPending}
                title="转接人工客服"
                aria-label="转接人工客服"
                className="flex items-center gap-1 px-2 h-7 rounded-lg
                  border border-border-subtle text-[11px] text-text-secondary
                  hover:text-text-primary hover:border-accent/40
                  disabled:cursor-not-allowed disabled:opacity-50 transition-colors"
              >
                <UserRound size={13} />
                <span className="hidden sm:inline">转人工</span>
              </button>
            )}
            <button
              onClick={() => newSession()}
              title="新客服会话"
              aria-label="新客服会话"
              className="flex items-center justify-center w-7 h-7 rounded-lg
                border border-border-subtle text-text-secondary
                hover:text-text-primary hover:border-accent/40 transition-colors"
            >
              <Plus size={14} />
            </button>
            {!standalone && (
              <button
                onClick={onClose}
                title="收起客服窗口"
                aria-label="收起客服窗口"
                className="flex items-center justify-center w-7 h-7 rounded-lg text-text-secondary
                  hover:text-text-primary hover:bg-surface-elevated transition-colors"
              >
                <X size={16} />
              </button>
            )}
          </div>
        </div>

        {/* Error toast */}
        {error && (
          <div className="shrink-0 mx-3 mt-2 px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-xs text-red-600 flex items-center gap-2">
            <span className="flex-1 min-w-0 break-words">{error}</span>
            <button onClick={() => setError(null)} className="shrink-0 text-red-400 hover:text-red-600">
              关闭
            </button>
          </div>
        )}

        {/* Content */}
        <div className="flex-1 flex flex-col min-h-0">
          {hasMessages ? (
            <>
              <CSMessageList
                messages={messages}
                isLoading={isLoading}
                currentNode={currentNode}
                timeline={csTimeline}
              />
              {handoffState !== 'none' && <CSHandoffCard handoffState={handoffState} />}
              {handoffState === 'waiting' && waitRemaining !== null && (
                <div className="px-3 mb-3">
                  <div className="rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-800">
                    ⏳ 人工接入中 · 剩余 {waitRemaining} 秒
                    {handoffMeta && handoffMeta.queue_position > 0
                      ? `（前面 ${handoffMeta.queue_position} 位等待）`
                      : ''}
                    ，超时将自动为您登记工单
                  </div>
                </div>
              )}
              {pendingProposal && (
                <CSConfirmCard
                  pending={pendingProposal as PendingActionSnapshot}
                  busy={confirmBusy}
                  disabled={handoffOwnsConversation}
                  onConfirm={() => handleConfirmAction('confirm')}
                  onCancel={() => handleConfirmAction('cancel')}
                />
              )}
              {candidateOptions && candidateOptions.length > 0 && (
                <div className="px-3 mb-4 ml-11">
                  <div className="bg-sky-50 border border-sky-200 rounded-xl px-4 py-3">
                    <p className="text-xs font-medium text-sky-700 mb-2">
                      点选要办理的订单（也可直接回复序号）
                    </p>
                    <div className="flex flex-col gap-2">
                      {candidateOptions.map((opt, i) => (
                        <button
                          key={opt.id || i}
                          onClick={() => handleSend(opt.label)}
                          disabled={isLoading || handoffOwnsConversation}
                          className="text-left text-sm text-sky-900 bg-white border
                            border-sky-200 rounded-lg px-3 py-2 hover:bg-sky-100
                            transition-colors disabled:opacity-50"
                        >
                          {opt.label}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              )}
              {showRatingCard && (
                <CSSatisfactionCard
                  conversationId={currentId}
                  onDismissed={() => setShowSatisfaction(false)}
                />
              )}
            </>
          ) : (
            <>
              {handoffState !== 'none' && <CSHandoffCard handoffState={handoffState} />}
              <CSWelcome
                onQuickPrompt={handleSend}
                onRequestHandoff={handleRequestHandoff}
                handoffDisabled={!hasMessages || isLoading || handoffState !== 'none'}
                handoffPending={handoffPending}
              />
              {tickets.length > 0 && (
                <div className="px-3 mt-2">
                  <div className="rounded-xl border border-border-subtle bg-white overflow-hidden">
                    <button
                      onClick={() => setTicketsOpen(!ticketsOpen)}
                      className="w-full flex items-center justify-between px-3 py-2 text-xs text-text-secondary hover:bg-black/[0.03]"
                    >
                      <span>📋 我的工单（{tickets.length}）</span>
                      <span className="text-text-muted">{ticketsOpen ? '收起' : '展开'}</span>
                    </button>
                    {ticketsOpen && (
                      <div className="divide-y divide-black/[0.04]">
                        {tickets.map((t) => (
                          <div key={t.ticket_id} className="px-3 py-2">
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-[11px] font-mono text-text-muted shrink-0">{t.ticket_id}</span>
                              <span className={`shrink-0 text-[10px] rounded-full px-1.5 py-0.5 ${
                                t.status === 'open' || t.status === 'processing'
                                  ? 'bg-blue-50 text-blue-700'
                                  : t.status === 'resolved'
                                    ? 'bg-emerald-50 text-emerald-700'
                                    : 'bg-black/5 text-text-muted'
                              }`}>
                                {t.status === 'open' ? '待处理' : t.status === 'processing' ? '处理中'
                                  : t.status === 'pending_user' ? '待补充' : t.status === 'resolved' ? '已解决' : '已关闭'}
                              </span>
                            </div>
                            <p className="text-[12px] text-text-primary mt-0.5 break-words">{t.title || t.type}</p>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        {/* 「坐席正在输入」指示（handoffState='active' 才有意义） */}
        {agentTyping && handoffState === 'active' && (
          <div className="shrink-0 px-4 pb-1 flex items-center gap-1.5
            text-[11px] text-text-muted">
            <span className="flex gap-0.5" aria-hidden>
              <i className="w-1 h-1 rounded-full bg-accent animate-bounce" />
              <i className="w-1 h-1 rounded-full bg-accent animate-bounce [animation-delay:120ms]" />
              <i className="w-1 h-1 rounded-full bg-accent animate-bounce [animation-delay:240ms]" />
            </span>
            坐席正在输入…
          </div>
        )}

        {/* Status bar */}
        <CSStatusBar
          currentStatus={currentStatus}
          isLoading={isLoading}
          intentDetected={intentDetected}
          handoffState={handoffState}
        />

        {/* Input */}
        <CSInput
          onSend={handleSend}
          onStop={stopStream}
          isLoading={isLoading}
          disabled={handoffOwnsConversation}
          onTyping={handleUserTyping}
          draft={prefillDraft}
        />
      </div>
    </>
  )
}
