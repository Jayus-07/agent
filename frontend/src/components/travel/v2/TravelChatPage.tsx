'use client'

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { nanoid } from 'nanoid'
import { ArrowLeft, ArrowRight, CalendarDays, CircleAlert, LoaderCircle, MapPin, Send, Sparkles } from 'lucide-react'
import { streamTravelPlanV2, type TravelStreamEvent } from '@/api/travel'
import { getCachedUser } from '@/lib/auth'
import {
  createTravelV2IdempotencyKey,
  createTravelTripV2,
  fetchTravelTripV2,
  saveTravelTripDocumentV2,
  type CreateTravelTripV2,
  type EditTravelTripV2,
  type TravelTripV2,
} from '@/api/travelV2'
import { initialTravelProcess, reduceTravelStreamEvent, travelProcessStatusLabel, type TravelProcessState } from '@/components/travel/travelRuntime'
import { useTravelConversation, type TravelConversationMessage } from '@/components/travel/useTravelConversation'
import { TravelBottomNav, TravelTopBar } from './TravelV2Frame'
import { resolveTravelStreamOutcome } from './travelLiveRuntime'
import { confirmedTimezoneFromTrip, itineraryToTripDocumentV2 } from './travelV2PlannerAdapter'
import {
  classifyTravelWriteError,
  isConfirmedCreatedTravelTrip,
  isConfirmedTravelWrite,
  nextPaint,
  travelWriteErrorMessage,
  travelWriteStatusLabel,
  travelWriteStatusRole,
  travelWriteStatusTone,
  type TravelWriteStatus,
} from './travelV2WriteState'

const SUGGESTIONS = ['推荐杭州适合慢游的景点', '帮我安排三天行程', '第二天想少走一点']
const DECISION_TOKEN_MARKER = /\n\[\[travel-decision-token:([a-f0-9]{24})\]\]$/

function pendingDecisionTokenFromMessages(messages: TravelConversationMessage[]): string | undefined {
  const latestAssistant = [...messages].reverse().find((message) => message.role === 'assistant')
  return latestAssistant?.text.match(DECISION_TOKEN_MARKER)?.[1]
}

function pendingDecisionTokenFromResponse(response: { travel_context?: { pending_decision?: { decision_token?: string } } }): string | undefined {
  const token = response.travel_context?.pending_decision?.decision_token
  return typeof token === 'string' && /^[a-f0-9]{24}$/.test(token) ? token : undefined
}

function withPendingDecisionToken(text: string, token?: string): string {
  return token ? `${text}\n[[travel-decision-token:${token}]]` : text
}

function visibleTravelMessage(text: string): string {
  return text.replace(DECISION_TOKEN_MARKER, '')
}

type PendingTravelPlanWrite =
  | { kind: 'create'; key: string; input: CreateTravelTripV2; runId: string; replyText: string }
  | { kind: 'edit'; key: string; tripId: string; expectedRevision: number; input: EditTravelTripV2; baseTrip: TravelTripV2; runId: string; replyText: string }

export default function TravelChatPage({ initialPrompt = '', initialConversationId = '' }: { initialPrompt?: string; initialConversationId?: string }) {
  const [conversationId] = useState(() => initialConversationId || `travel-${nanoid(18)}`)
  const { messages, setMessages, syncError } = useTravelConversation(conversationId)
  const [draft, setDraft] = useState(initialPrompt)
  const [loading, setLoading] = useState(false)
  const [process, setProcess] = useState<TravelProcessState | null>(null)
  const [error, setError] = useState('')
  const [trip, setTrip] = useState<TravelTripV2 | null>(null)
  const tripRef = useRef<TravelTripV2 | null>(null)
  tripRef.current = trip
  const pendingPlanWriteRef = useRef<PendingTravelPlanWrite | null>(null)
  const planWriteLockRef = useRef(false)
  const [writeFeedback, setWriteFeedback] = useState<{ status: TravelWriteStatus; message: string } | null>(null)
  const [hasPlan, setHasPlan] = useState(false)
  const [tripLoadNotice, setTripLoadNotice] = useState('')
  const [tripAssociationReady, setTripAssociationReady] = useState(false)
  const [associatedTripId, setAssociatedTripId] = useState('')
  const abortRef = useRef<AbortController | null>(null)
  const orderedMessages = useMemo(() => messages, [messages])

  const persistPlanWrite = useCallback(async (intent: PendingTravelPlanWrite): Promise<boolean> => {
    if (planWriteLockRef.current) return false
    planWriteLockRef.current = true
    setWriteFeedback({ status: 'VALIDATING', message: '正在核对行程数据…' })
    await nextPaint()
    setWriteFeedback({ status: 'SAVING', message: '正在请求服务端校验并提交正式行程…' })
    try {
      let savedTrip: TravelTripV2
      if (intent.kind === 'create') {
        const result = await createTravelTripV2(intent.input, intent.key)
        if (!isConfirmedCreatedTravelTrip(result)) {
          setWriteFeedback({ status: 'UNKNOWN', message: '创建回执尚不能证明正式行程已提交；不会显示已保存。请用同一请求安全重试。' })
          return false
        }
        savedTrip = result
      } else {
        const result = await saveTravelTripDocumentV2(intent.tripId, intent.input, intent.key)
        if (!isConfirmedTravelWrite(result, intent.tripId, intent.expectedRevision)) {
          setWriteFeedback({ status: 'UNKNOWN', message: '保存回执尚不能证明事务已提交；页面保留上次确认的 Revision。请用同一请求安全重试。' })
          return false
        }
        savedTrip = { ...intent.baseTrip, revision: result.revision, document: result.document, updated_at: new Date().toISOString() }
      }

      const current = tripRef.current
      if (intent.kind === 'create') {
        if (!current || current.trip_id !== savedTrip.trip_id || current.revision < savedTrip.revision) {
          tripRef.current = savedTrip
          setTrip(savedTrip)
        }
      } else if (current?.trip_id === intent.tripId && current.revision === intent.expectedRevision) {
        tripRef.current = savedTrip
        setTrip(savedTrip)
      }
      pendingPlanWriteRef.current = null
      setHasPlan(true)
      setTripLoadNotice('')
      setV2TripAssociation(conversationId, savedTrip.trip_id)
      setError('')
      const latest = tripRef.current
      const currentNotice = latest && latest.trip_id === savedTrip.trip_id && latest.revision > savedTrip.revision
        ? ` 页面已有更新的 v${latest.revision}，已保留该版本。`
        : ''
      const timezoneNotice = savedTrip.document.health.issues.some((issue) => issue.code === 'timezone_unconfirmed')
        ? '目的地时区尚未确认；UTC 仅为格式占位，行程当地时间可能不准确。'
        : ''
      setWriteFeedback({ status: 'SAVED', message: `服务端已确认保存到 v${savedTrip.revision}.${currentNotice}${timezoneNotice ? ` ${timezoneNotice}` : ''}` })
      setMessages((currentMessages) => [...currentMessages, {
        id: `${intent.runId}:assistant`, conversationId, turnId: intent.runId,
        role: 'assistant', text: `${intent.replyText}\n\n行程已自动保存到 V2 · v${savedTrip.revision}。${currentNotice}${timezoneNotice ? ` ${timezoneNotice}` : ''}`,
        tag: '行程已自动保存', tone: 'ok',
      }])
      return true
    } catch (cause) {
      const status = classifyTravelWriteError(cause)
      setWriteFeedback({ status, message: travelWriteErrorMessage(cause, status) })
      if (status !== 'UNKNOWN') pendingPlanWriteRef.current = null
      return false
    } finally {
      planWriteLockRef.current = false
    }
  }, [conversationId, setMessages])

  const retryPendingPlanWrite = useCallback(async () => {
    const intent = pendingPlanWriteRef.current
    if (!intent || writeFeedback?.status !== 'UNKNOWN') return false
    return persistPlanWrite(intent)
  }, [persistPlanWrite, writeFeedback?.status])

  const refreshPlanConflict = useCallback(async () => {
    const current = tripRef.current
    if (!current) return false
    try {
      const latest = await fetchTravelTripV2(current.trip_id)
      if (latest.trip_id !== current.trip_id) throw new Error('服务器返回了不匹配的行程。')
      if (tripRef.current?.trip_id === latest.trip_id && tripRef.current.revision < latest.revision) {
        tripRef.current = latest
        setTrip(latest)
      }
      setWriteFeedback({ status: 'CONFLICT', message: `已载入服务器最新 v${latest.revision}；原修改未自动重放，请重新规划。` })
      return true
    } catch (cause) {
      setWriteFeedback({ status: 'CONFLICT', message: `刷新最新行程失败：${cause instanceof Error ? cause.message : '请检查网络后重试。'}` })
      return false
    }
  }, [])

  const sendText = useCallback(async (raw: string) => {
    const text = raw.trim()
    if (!text || loading || !tripAssociationReady || pendingPlanWriteRef.current) return
    const pendingDecisionToken = pendingDecisionTokenFromMessages(messages)
    const clientRunId = `client-${Date.now()}-${nanoid(8)}`
    const controller = new AbortController()
    abortRef.current = controller
    setLoading(true)
    setError('')
    setWriteFeedback(null)
    setDraft('')
    setProcess(initialTravelProcess(clientRunId))
    setMessages((current) => [...current, {
      id: `${clientRunId}:user`, conversationId, turnId: clientRunId, role: 'user', text,
    }])
    const events: TravelStreamEvent[] = []
    try {
      for await (const event of streamTravelPlanV2(text, conversationId, {
        signal: controller.signal,
        clientRunId,
        mode: 'chat',
        source: 'manual',
        ...(pendingDecisionToken
          ? { actionPayload: { decision_token: pendingDecisionToken } }
          : {}),
      })) {
        events.push(event)
        setProcess((current) => current ? reduceTravelStreamEvent(current, event) : current)
      }
      const outcome = resolveTravelStreamOutcome(events)
      if (outcome.kind === 'failed') throw new Error(outcome.message)

      const response = outcome.response
      const replyText = response.final_answer?.trim() || '已收到，我可以继续帮你调整旅行安排。'
      const responseDecisionToken = pendingDecisionTokenFromResponse(response)
      let tag = '旅游问答'
      const tone: TravelConversationMessage['tone'] = 'ok'
      if (outcome.kind === 'clarification') tag = '需要补充信息'
      if (outcome.kind === 'plan' && response.itinerary) {
        const document = itineraryToTripDocumentV2(response.itinerary, confirmedTimezoneFromTrip(trip))
        if (trip) {
          const expectedRevision = trip.revision
          const intent: PendingTravelPlanWrite = {
            kind: 'edit', key: createTravelV2IdempotencyKey(), tripId: trip.trip_id,
            expectedRevision,
            input: {
              expected_revision: expectedRevision,
              command_type: 'ai_edit',
              change_summary: text.slice(0, 500) || '根据旅游对话更新行程',
              document,
            },
            baseTrip: trip,
            runId: clientRunId,
            replyText,
          }
          pendingPlanWriteRef.current = intent
          await persistPlanWrite(intent)
        } else {
          if (associatedTripId) {
            throw new Error('关联的 V2 行程无法读取，本次规划结果没有保存。请刷新后重试。')
          }
          const intent: PendingTravelPlanWrite = {
            kind: 'create',
            key: createTravelV2IdempotencyKey(),
            input: { title: `${document.brief.destination} ${document.brief.day_count} 天行程`, document },
            runId: clientRunId,
            replyText,
          }
          pendingPlanWriteRef.current = intent
          await persistPlanWrite(intent)
        }
        return
      }
      setMessages((current) => [...current, {
        id: `${clientRunId}:assistant`, conversationId, turnId: clientRunId,
        role: 'assistant', text: withPendingDecisionToken(replyText, responseDecisionToken), tag, tone,
      }])
    } catch (cause) {
      if (controller.signal.aborted) return
      const message = cause instanceof Error ? cause.message : '旅游服务暂时不可用，请稍后重试。'
      setError(message)
      setMessages((current) => [...current, {
        id: `${clientRunId}:assistant`, conversationId, turnId: clientRunId,
        role: 'assistant',
        text: withPendingDecisionToken(`这次请求没有完成：${message}`, pendingDecisionToken),
        tag: '请求失败', tone: 'warn',
      }])
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      setLoading(false)
    }
  }, [associatedTripId, conversationId, loading, messages, persistPlanWrite, setMessages, trip, tripAssociationReady])

  useEffect(() => {
    let cancelled = false
    const tripId = getV2TripAssociation(conversationId)
    setTripAssociationReady(false)
    setAssociatedTripId(tripId)
    if (!tripId) {
      setTripAssociationReady(true)
      return
    }
    void fetchTravelTripV2(tripId).then((savedTrip) => {
      if (cancelled) return
      setTrip(savedTrip)
      setHasPlan(savedTrip.status === 'active')
    }).catch(() => {
      if (!cancelled) setTripLoadNotice('关联的 V2 行程暂时无法读取；本轮保存仍会按服务端版本校验。')
    }).finally(() => { if (!cancelled) setTripAssociationReady(true) })
    return () => { cancelled = true }
  }, [conversationId])

  useEffect(() => () => abortRef.current?.abort(), [])

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void sendText(draft)
  }

  const processLabel = process ? travelProcessStatusLabel({
    loading,
    stopped: false,
    status: process.status,
  }) : ''

  return (
    <main className="flex min-h-screen flex-col bg-[#f4f8fd] pb-[68px] text-[#17283f] md:h-screen md:min-h-0 md:pb-0">
      <div className="hidden md:block"><TravelTopBar /></div>
      <header className="sticky top-0 z-20 flex h-[58px] items-center justify-between border-b border-[#e6edf5] bg-white px-4 md:mx-auto md:mb-3 md:h-[64px] md:w-full md:max-w-[940px] md:rounded-2xl md:border md:px-5">
        <a href="/travel" aria-label="返回旅游首页" className="flex h-9 w-9 items-center justify-center rounded-full text-[#536984] hover:bg-[#f3f7fc]"><ArrowLeft size={18} aria-hidden /></a>
        <div className="text-center"><p className="text-sm font-semibold text-[#263c57]">AI 旅行助手</p><p className="mt-0.5 text-[9px] text-[#94a2b3]">灵感、路线和行程调整</p></div>
        {hasPlan && trip ? <a href={`/travel/itineraries/${encodeURIComponent(trip.trip_id)}`} className="inline-flex h-9 items-center gap-1.5 rounded-full bg-[#2878f5] px-3 text-[10px] font-semibold text-white shadow-sm"><CalendarDays size={13} aria-hidden />查看行程 · v{trip.revision}</a> : <a href="/travel/itineraries" className="inline-flex h-9 items-center gap-1.5 rounded-full border border-[#e3ebf5] px-3 text-[10px] font-medium text-[#6e8097]"><CalendarDays size={13} aria-hidden />我的行程</a>}
      </header>
      <div className="mx-auto flex min-h-0 w-full max-w-[940px] flex-1 flex-col px-3 py-3 md:px-0 md:py-0">
        <section aria-label="旅行对话" className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[22px] border border-[#e4ebf4] bg-white shadow-[0_10px_34px_rgba(31,74,130,.06)] md:rounded-[24px]">
          <div className="flex-1 space-y-4 overflow-y-auto p-4 md:p-6" aria-live="polite">
            {orderedMessages.length === 0 && <div className="flex items-start gap-3"><span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-[#eaf2ff] text-[#2878f5]"><Sparkles size={16} aria-hidden /></span><div className="max-w-[88%] rounded-2xl rounded-tl-md bg-[#f2f7fd] px-4 py-3 text-xs leading-6 text-[#405875] md:max-w-[76%] md:text-sm">你好，我可以和你一起规划旅行。告诉我目的地、同行人数、预算和喜欢的节奏；也可以直接问景点、天气或交通。<div className="mt-3 flex flex-wrap gap-2">{SUGGESTIONS.map((suggestion) => <button key={suggestion} type="button" onClick={() => setDraft(suggestion)} className="rounded-full border border-[#dce8f7] bg-white px-3 py-1.5 text-[10px] text-[#587494] transition hover:border-[#9cc2f4] hover:text-[#2878f5]">{suggestion}</button>)}</div></div></div>}
            {orderedMessages.map((message) => <div key={message.id} className={`flex items-start gap-3 ${message.role === 'user' ? 'justify-end' : ''}`}>{message.role === 'assistant' && <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-[#eaf2ff] text-[#2878f5]"><Sparkles size={16} aria-hidden /></span>}<div className={`max-w-[88%] whitespace-pre-wrap rounded-2xl px-4 py-3 text-xs leading-6 md:max-w-[76%] md:text-sm ${message.role === 'user' ? 'rounded-tr-md bg-[#2878f5] text-white' : 'rounded-tl-md bg-[#f2f7fd] text-[#405875]'}`}>{message.role === 'assistant' && message.tag && <span className={`mb-1 block text-[9px] font-semibold ${message.tone === 'warn' ? 'text-amber-700' : 'text-[#4882ca]'}`}>{message.tag}</span>}{visibleTravelMessage(message.text)}{message.role === 'assistant' && message.tag === '行程已自动保存' && trip && <a href={`/travel/itineraries/${encodeURIComponent(trip.trip_id)}`} className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-white px-3 py-2 text-[10px] font-semibold text-[#2878f5] shadow-sm">打开行程工作台 <ArrowRight size={12} aria-hidden /></a>}</div></div>)}
            {loading && <div className="flex items-start gap-3"><span className="mt-0.5 flex h-8 w-8 items-center justify-center rounded-xl bg-[#eaf2ff] text-[#2878f5]"><Sparkles size={16} aria-hidden /></span><div className="max-w-[88%] rounded-2xl rounded-tl-md bg-[#f2f7fd] px-4 py-3 text-xs text-[#526984]"><div className="flex items-center gap-2"><LoaderCircle size={14} className="animate-spin text-[#2878f5]" aria-hidden />{processLabel || '正在连接旅游 Agent…'}</div>{process && Object.keys(process.stages).length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{Object.entries(process.stages).map(([name, stage]) => <span key={name} className={`rounded-full px-2 py-1 text-[9px] ${stage.status === 'completed' ? 'bg-emerald-50 text-emerald-700' : stage.status === 'failed' ? 'bg-rose-50 text-rose-700' : 'bg-white text-[#6e8097]'}`}>{name}</span>)}</div>}</div></div>}
            {error && <div role="alert" className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5 text-[10px] leading-5 text-rose-800"><CircleAlert size={14} className="mt-0.5 shrink-0" aria-hidden />{error}</div>}
            {writeFeedback && <div role={travelWriteStatusRole(writeFeedback.status)} className={`rounded-xl border px-3 py-2.5 text-sm leading-5 ${travelWriteStatusTone(writeFeedback.status)}`}>
              <p><span className="mr-1.5 font-semibold">{travelWriteStatusLabel(writeFeedback.status)}</span>{writeFeedback.message}</p>
              {writeFeedback.status === 'UNKNOWN' && pendingPlanWriteRef.current && <button type="button" onClick={() => void retryPendingPlanWrite()} className="mt-2 min-h-10 rounded-lg border border-amber-300 bg-white px-3 text-sm font-semibold text-amber-950">使用同一请求安全重试</button>}
              {writeFeedback.status === 'CONFLICT' && trip && <button type="button" onClick={() => void refreshPlanConflict()} className="mt-2 min-h-10 rounded-lg border border-amber-300 bg-white px-3 text-sm font-semibold text-amber-950">刷新最新行程并重新规划</button>}
            </div>}
            {syncError && <p role="status" className="text-[10px] text-amber-700">{syncError}</p>}
            {tripLoadNotice && <p role="status" className="text-[10px] text-amber-700">{tripLoadNotice}</p>}
            {orderedMessages.length > 0 && <div className="flex items-center gap-2 text-[10px] text-[#9aa8b8]"><MapPin size={12} aria-hidden />对话消息会按账号和行程保存；服务端行程状态以行程详情为准。</div>}
          </div>
          <form onSubmit={submit} className="shrink-0 border-t border-[#edf1f6] p-3 md:p-4"><label className="sr-only" htmlFor="travel-chat-input">输入旅行需求</label><div className="flex items-end gap-2 rounded-2xl border border-[#dfe8f3] bg-[#fbfdff] p-2 focus-within:border-[#99bdfa] focus-within:ring-4 focus-within:ring-[#2878f5]/[.08]"><textarea id="travel-chat-input" aria-label="输入旅行需求" rows={2} value={draft} onChange={(event) => setDraft(event.target.value)} disabled={loading || !tripAssociationReady || writeFeedback?.status === 'UNKNOWN'} placeholder="接着提问，或告诉我你想怎么调整…" className="max-h-32 min-h-11 flex-1 resize-none bg-transparent px-2 py-2 text-xs leading-5 text-[#344b67] outline-none placeholder:text-[#9aa8b8] disabled:opacity-60 md:text-sm" /><button type="submit" aria-label="发送消息" disabled={!draft.trim() || loading || !tripAssociationReady || writeFeedback?.status === 'UNKNOWN'} className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#2878f5] text-white transition hover:bg-[#1769e9] disabled:cursor-not-allowed disabled:bg-[#c7d7ed]"><Send size={16} aria-hidden /></button></div><div className="mt-2 flex items-center justify-between px-1"><span className="text-[9px] text-[#9aa8b8]">消息提交至旅游 Agent · 成功结果自动保存为 V2 行程</span><a href={hasPlan && trip ? `/travel/itineraries/${encodeURIComponent(trip.trip_id)}` : '/travel/itineraries'} className="inline-flex items-center gap-1 text-[10px] font-medium text-[#2878f5]">{hasPlan && trip ? '查看当前行程' : '浏览历史行程'} <ArrowRight size={12} aria-hidden /></a></div></form>
        </section>
      </div>
      <TravelBottomNav active="home" />
    </main>
  )
}

function v2TripAssociationKey(conversationId: string): string {
  const user = getCachedUser() as Record<string, unknown> | null
  const tenant = String(user?.tenantId || user?.tenant_id || 'default')
  const owner = String(user?.userId || user?.user_id || user?.username || 'anonymous')
  return `travel:v2:trip:${encodeURIComponent(tenant)}:${encodeURIComponent(owner)}:${encodeURIComponent(conversationId)}`
}

function getV2TripAssociation(conversationId: string): string {
  try { return window.localStorage.getItem(v2TripAssociationKey(conversationId)) || '' } catch { return '' }
}

function setV2TripAssociation(conversationId: string, tripId: string): void {
  try { window.localStorage.setItem(v2TripAssociationKey(conversationId), tripId) } catch { /* 本地索引不可用时，行程仍已保存到服务器。 */ }
}
