'use client'

import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { nanoid } from 'nanoid'
import { ArrowLeft, ArrowRight, CalendarDays, CircleAlert, LoaderCircle, MapPin, Send, Sparkles } from 'lucide-react'
import { streamTravelPlanV2, parseTravelToolCard, type TravelClarificationOption, type TravelStreamEvent, type TravelToolCard } from '@/api/travel'
import {
  createTravelV2IdempotencyKey,
  createTravelTripV2,
  fetchTravelTripV2,
  fetchTravelTripConversationV2,
  fetchTravelTripRevisionsV2,
  type CreateTravelTripV2,
  type TravelTripV2,
} from '@/api/travelV2'
import { initialTravelProcess, reduceTravelStreamEvent, travelProcessStatusLabel, type TravelProcessState } from '@/components/travel/travelRuntime'
import ToolProcessRows from '@/components/travel/ToolProcessRows'
import { useTravelConversation, type TravelConversationMessage } from '@/components/travel/useTravelConversation'
import { TravelBottomNav, TravelTopBar } from './TravelV2Frame'
import { resolveTravelStreamOutcome } from './travelLiveRuntime'
import { ToolCardBlock } from './ToolCardBlock'
import TravelV2TripConversation from './TravelV2TripConversation'
import { defaultTravelV2ConversationId, getTravelV2TripAssociation, setTravelV2TripAssociation, travelV2TripIdsMatch, travelV2WorkspaceHref, travelV2WorkspaceRestoreHref } from './travelV2Conversation'
import { itineraryToTripDocumentV2 } from './travelV2PlannerAdapter'
import {
  classifyTravelWriteError,
  isConfirmedCreatedTravelTrip,
  nextPaint,
  travelWriteErrorMessage,
  travelWriteStatusLabel,
  travelWriteStatusRole,
  travelWriteStatusTone,
  type TravelWriteStatus,
} from './travelV2WriteState'

const SUGGESTIONS = ['推荐杭州适合慢游的景点', '帮我安排三天行程', '第二天想少走一点']
const DECISION_TOKEN_MARKER = /\n\[\[travel-decision-token:([a-f0-9]{24})\]\]$/
const DECISION_TOKEN_FIELD = 'decision_token'

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

type PendingTravelPlanWrite = { key: string; input: CreateTravelTripV2; runId: string; replyText: string }

/** 原型 A5 语义：SSE 完成后先出逐日草稿预览，用户确认才落正式行程（v1）。 */
type DraftPlanPreview = { runId: string; replyText: string; input: CreateTravelTripV2 }

export default function TravelChatPage({ initialPrompt = '', initialConversationId = '', initialTripId = '' }: { initialPrompt?: string; initialConversationId?: string; initialTripId?: string }) {
  const [conversationId, setConversationId] = useState(() => initialConversationId || (initialTripId
    ? defaultTravelV2ConversationId(initialTripId) : `travel-${nanoid(18)}`))
  const candidateConversationIdRef = useRef(initialConversationId || conversationId)
  const [conversationReady, setConversationReady] = useState(false)
  const { messages, setMessages, syncError } = useTravelConversation(conversationId, {
    enabled: conversationReady,
  })
  const [draft, setDraft] = useState(initialPrompt)
  const [loading, setLoading] = useState(false)
  const [process, setProcess] = useState<TravelProcessState | null>(null)
  const [error, setError] = useState('')
  const [draftPlan, setDraftPlan] = useState<DraftPlanPreview | null>(null)
  const [clarifyOptions, setClarifyOptions] = useState<{ runId: string; options: TravelClarificationOption[] } | null>(null)
  // F18：每轮对话的查询结果卡（tool.card 事件；SSE 会话内瞬时，历史消息不带卡）
  const [turnCards, setTurnCards] = useState<{ runId: string; cards: TravelToolCard[] }[]>([])
  const [trip, setTrip] = useState<TravelTripV2 | null>(null)
  const tripRef = useRef<TravelTripV2 | null>(null)
  tripRef.current = trip
  const pendingPlanWriteRef = useRef<PendingTravelPlanWrite | null>(null)
  const planWriteLockRef = useRef(false)
  const [writeFeedback, setWriteFeedback] = useState<{ status: TravelWriteStatus; message: string } | null>(null)
  const [tripLoadNotice, setTripLoadNotice] = useState('')
  const [conversationNotice, setConversationNotice] = useState('')
  const [tripAssociationReady, setTripAssociationReady] = useState(false)
  const [restoreAttempt, setRestoreAttempt] = useState(0)
  const [associatedTripId, setAssociatedTripId] = useState(initialTripId)
  const abortRef = useRef<AbortController | null>(null)
  const orderedMessages = useMemo(() => messages, [messages])
  const cardsForTurn = useCallback(
    (turnId: string) => turnCards.find((entry) => entry.runId === turnId)?.cards ?? [],
    [turnCards],
  )

  useEffect(() => {
    if (typeof window === 'undefined') return
    if (associatedTripId && !conversationReady) return
    const url = new URL(window.location.href)
    let changed = false
    if (url.searchParams.get('conversation_id') !== conversationId) {
      url.searchParams.set('conversation_id', conversationId)
      changed = true
    }
    if (associatedTripId && url.searchParams.get('trip_id') !== associatedTripId) {
      url.searchParams.set('trip_id', associatedTripId)
      changed = true
    }
    if (changed) window.history.replaceState(window.history.state, '', url.toString())
  }, [associatedTripId, conversationId, conversationReady])

  const persistPlanWrite = useCallback(async (intent: PendingTravelPlanWrite): Promise<boolean> => {
    if (planWriteLockRef.current) return false
    planWriteLockRef.current = true
    setWriteFeedback({ status: 'VALIDATING', message: '正在核对行程数据…' })
    await nextPaint()
    setWriteFeedback({ status: 'SAVING', message: '正在请求服务端校验并提交正式行程…' })
    try {
      const savedTrip = await createTravelTripV2(intent.input, intent.key)
      if (!isConfirmedCreatedTravelTrip(savedTrip)) {
        setWriteFeedback({ status: 'UNKNOWN', message: '创建回执尚不能证明正式行程已提交；不会显示已保存。请用同一请求安全重试。' })
        return false
      }

      const current = tripRef.current
      if (!current || current.trip_id !== savedTrip.trip_id || current.revision < savedTrip.revision) {
        tripRef.current = savedTrip
        setTrip(savedTrip)
      }
      pendingPlanWriteRef.current = null
      setTripLoadNotice('')
      setAssociatedTripId(savedTrip.trip_id)
      setTripAssociationReady(true)
      const resolvedConversationId = savedTrip.conversation_id || conversationId
      const conversationChanged = resolvedConversationId !== conversationId
      if (conversationChanged) {
        setConversationNotice('服务端恢复的会话与当前链接不同，已切换到该行程的原始会话。')
        setConversationId(resolvedConversationId)
      }
      setConversationReady(true)
      setTravelV2TripAssociation(resolvedConversationId, savedTrip.trip_id)
      setError('')
      const latest = tripRef.current
      const currentNotice = latest && latest.trip_id === savedTrip.trip_id && latest.revision > savedTrip.revision
        ? ` 页面已有更新的 v${latest.revision}，已保留该版本。`
        : ''
      const timezoneNotice = savedTrip.document.health.issues.some((issue) => issue.code === 'timezone_unconfirmed')
        ? '目的地时区尚未确认；UTC 仅为格式占位，行程当地时间可能不准确。'
        : ''
      setWriteFeedback({ status: 'SAVED', message: `服务端已确认保存到 v${savedTrip.revision}.${currentNotice}${timezoneNotice ? ` ${timezoneNotice}` : ''}` })
      if (!conversationChanged) {
        setMessages((current) => [...current, {
          id: `${intent.runId}:assistant`, conversationId, turnId: intent.runId,
          role: 'assistant', text: `${intent.replyText}\n\n行程已保存到 V2 · v${savedTrip.revision}。${currentNotice}${timezoneNotice ? ` ${timezoneNotice}` : ''}`,
          tag: '行程已保存', tone: 'ok',
        }])
      }
      setDraftPlan(null)
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

  /** 草稿确认保存：首次点击生成幂等键；UNKNOWN 后走「同一请求安全重试」，不得另起请求。 */
  const saveDraftPlan = useCallback(async () => {
    if (!draftPlan || planWriteLockRef.current || writeFeedback?.status === 'UNKNOWN') return
    const intent: PendingTravelPlanWrite = {
      key: createTravelV2IdempotencyKey(),
      input: draftPlan.input,
      runId: draftPlan.runId,
      replyText: draftPlan.replyText,
    }
    pendingPlanWriteRef.current = intent
    await persistPlanWrite(intent)
  }, [draftPlan, persistPlanWrite, writeFeedback?.status])

  /** 撤下草稿：不落库，保留对话继续调整（再次生成会覆盖草稿）。 */
  const discardDraftPlan = useCallback(() => {
    if (planWriteLockRef.current) return
    setDraftPlan(null)
    setMessages((current) => [...current, {
      id: `draft-discard-${Date.now()}`, conversationId, turnId: `draft-discard-${Date.now()}`,
      role: 'assistant', text: '草稿已撤下，没有保存。继续告诉我你的调整想法，我会重新生成。',
      tag: '草稿未保存', tone: 'warn',
    }])
  }, [conversationId, setMessages])

  const sendText = useCallback(async (raw: string) => {
    const text = raw.trim()
    if (!text || trip || loading || !tripAssociationReady || !conversationReady || pendingPlanWriteRef.current) return
    const pendingDecisionToken = pendingDecisionTokenFromMessages(messages)
    const clientRunId = `client-${Date.now()}-${nanoid(8)}`
    const controller = new AbortController()
    abortRef.current = controller
    setLoading(true)
    setError('')
    setWriteFeedback(null)
    setClarifyOptions(null)
    setDraft('')
    setProcess(initialTravelProcess(clientRunId))
    setTurnCards((current) => [...current, { runId: clientRunId, cards: [] }])
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
          ? { actionPayload: { [DECISION_TOKEN_FIELD]: pendingDecisionToken } }
          : {}),
      })) {
        events.push(event)
        if (event.event === 'tool.card') {
          const card = parseTravelToolCard(event.data)
          if (card) {
            setTurnCards((current) => current.map((entry) => entry.runId === clientRunId
              ? { ...entry, cards: [...entry.cards, card] }
              : entry))
          }
        }
        setProcess((current) => current ? reduceTravelStreamEvent(current, event) : current)
      }
      const outcome = resolveTravelStreamOutcome(events)
      if (outcome.kind === 'failed') throw new Error(outcome.message)

      const response = outcome.response
      const replyText = response.final_answer?.trim() || '已收到，我可以继续帮你调整旅行安排。'
      const responseDecisionToken = pendingDecisionTokenFromResponse(response)
      let tag = '旅游问答'
      const tone: TravelConversationMessage['tone'] = 'ok'
      if (outcome.kind === 'clarification') {
        tag = '需要补充信息'
        // 原型 F01：后端已产出结构化澄清选项，V2 对话直接渲染成可点选项
        const options = Array.isArray(response.clarification_options) ? response.clarification_options : []
        setClarifyOptions(options.length ? { runId: clientRunId, options } : null)
      }
      if (outcome.kind === 'plan' && response.itinerary) {
        if (associatedTripId) {
          throw new Error('关联的 V2 行程无法读取，本次规划结果没有保存。请刷新后重试。')
        }
        // 原型 F03：先生成草稿预览，用户确认后才创建正式行程（不再自动保存）
        const document = itineraryToTripDocumentV2(response.itinerary)
        setDraftPlan({
          runId: clientRunId,
          replyText,
          input: {
            title: `${document.brief.destination} ${document.brief.day_count} 天行程`,
            document,
            conversation_id: conversationId,
          },
        })
        setMessages((current) => [...current, {
          id: `${clientRunId}:assistant`, conversationId, turnId: clientRunId,
          role: 'assistant', text: replyText, tag: '行程草稿 · 待确认保存', tone,
        }])
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
  }, [associatedTripId, conversationId, conversationReady, loading, messages, persistPlanWrite, setMessages, trip, tripAssociationReady])

  useEffect(() => {
    let cancelled = false
    const candidateConversationId = candidateConversationIdRef.current
    const tripId = initialTripId || getTravelV2TripAssociation(candidateConversationId)
    setTripAssociationReady(false)
    setConversationReady(false)
    setConversationNotice('')
    setTripLoadNotice('')
    setAssociatedTripId(tripId)
    if (!tripId) {
      setTrip(null)
      tripRef.current = null
      setConversationId(candidateConversationId)
      setTripAssociationReady(true)
      setConversationReady(true)
      return
    }
    if (tripRef.current && !travelV2TripIdsMatch(tripRef.current.trip_id, tripId)) {
      setTrip(null)
      tripRef.current = null
    }
    void fetchTravelTripV2(tripId).then(async (savedTrip) => {
      if (cancelled) return
      const association = await fetchTravelTripConversationV2(tripId)
      if (cancelled) return
      if (!travelV2TripIdsMatch(association.trip_id, savedTrip.trip_id)) {
        throw new Error('服务端返回的行程会话关联与当前行程不匹配。')
      }
      setTrip(savedTrip)
      tripRef.current = savedTrip
      setAssociatedTripId(savedTrip.trip_id)
      if (!association.linked || !association.conversation_id?.trim()) {
        setConversationNotice('该行程没有可验证的服务端原始会话关联；未读取链接中的会话或任何对话历史。')
        return
      }
      const resolvedConversationId = association.conversation_id
      if (candidateConversationId && candidateConversationId !== resolvedConversationId) {
        setConversationNotice('链接中的会话与该行程的服务端关联不一致，已恢复行程原始会话。')
      }
      setConversationId(resolvedConversationId)
      setTravelV2TripAssociation(resolvedConversationId, savedTrip.trip_id)
      setConversationReady(true)
    }).catch(() => {
      if (!cancelled) {
        setTrip(null)
        tripRef.current = null
        setTripLoadNotice('行程或原始会话关联暂时无法验证；为保护会话隔离，未加载任何对话历史，也不会创建或覆盖行程。请重试。')
      }
    }).finally(() => { if (!cancelled) setTripAssociationReady(true) })
    return () => { cancelled = true }
  }, [initialTripId, restoreAttempt])

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

  const refreshAfterAIEdit = useCallback(async (savedRevision: number) => {
    if (!associatedTripId) throw new Error('当前会话没有关联正式行程。')
    const [latest, history] = await Promise.all([
      fetchTravelTripV2(associatedTripId), fetchTravelTripRevisionsV2(associatedTripId),
    ])
    if (latest.trip_id !== associatedTripId || latest.revision < savedRevision
      || !history.revisions.some((item) => item.revision === savedRevision)) {
      throw new Error('服务器尚未返回 AI 编辑对应的正式 Revision。')
    }
    setTrip((current) => current?.trip_id === associatedTripId && current.revision > latest.revision ? current : latest)
  }, [associatedTripId])

  const refreshAssociatedTrip = useCallback(async () => {
    if (!associatedTripId) return
    const latest = await fetchTravelTripV2(associatedTripId)
    if (latest.trip_id === associatedTripId) setTrip((current) => current && current.revision > latest.revision ? current : latest)
  }, [associatedTripId])

  if (associatedTripId && !tripAssociationReady) {
    return <main className="flex min-h-screen items-center justify-center bg-[#f4f8fd] text-sm text-[#70839b]">正在恢复这条正式行程的对话…</main>
  }

  if (associatedTripId && tripAssociationReady && !trip) {
    return <main className="min-h-screen bg-[#f4f8fd] p-5 text-[#17283f]"><a href="/travel/itineraries" className="text-sm text-[#2878f5]">返回我的行程</a><p role="alert" className="mx-auto mt-8 max-w-lg rounded-xl bg-white p-5 text-sm text-rose-700">{tripLoadNotice || '关联的正式行程暂时无法读取；当前不会创建或覆盖行程。'}</p><button type="button" onClick={() => setRestoreAttempt((value) => value + 1)} className="mx-auto mt-4 block rounded-lg bg-[#2878f5] px-4 py-2 text-sm font-semibold text-white">重试读取</button></main>
  }

  if (trip && !conversationReady) {
    return <main className="min-h-screen bg-[#f4f8fd] p-5 text-[#17283f]"><a href="/travel/itineraries" className="text-sm text-[#2878f5]">返回我的行程</a><section className="mx-auto mt-8 max-w-lg rounded-xl bg-white p-5"><h1 className="text-base font-semibold">{trip.title || trip.document.brief.destination}</h1><p role="alert" className="mt-3 text-sm leading-6 text-amber-800">{conversationNotice || '服务端尚未验证该行程的原始会话关联；为保护会话隔离，没有加载对话历史。'}</p><a href={`/travel/itineraries/${encodeURIComponent(trip.trip_id)}`} className="mt-4 inline-flex min-h-10 items-center rounded-lg bg-[#2878f5] px-4 text-sm font-semibold text-white">查看正式行程</a></section></main>
  }

  if (trip) {
    return <main className="flex min-h-screen flex-col bg-[#f4f8fd] pb-[68px] text-[#17283f] md:h-screen md:min-h-0 md:pb-0">
      <div className="hidden md:block"><TravelTopBar /></div>
      <header className="sticky top-0 z-20 flex h-[58px] items-center justify-between border-b border-[#e6edf5] bg-white px-4 md:mx-auto md:mb-3 md:h-[64px] md:w-full md:max-w-[940px] md:rounded-2xl md:border md:px-5">
        <a href={travelV2WorkspaceHref(trip.trip_id, conversationId)} aria-label="打开正式行程工作台" className="flex h-9 w-9 items-center justify-center rounded-full text-[#536984] hover:bg-[#f3f7fc]"><ArrowLeft size={18} aria-hidden /></a>
        <div className="text-center"><p className="text-sm font-semibold text-[#263c57]">{trip.title || trip.document.brief.destination}</p><p className="mt-0.5 text-[9px] text-[#94a2b3]">正式行程对话 · Revision v{trip.revision}</p></div>
        <a href={travelV2WorkspaceHref(trip.trip_id, conversationId)} className="inline-flex h-9 items-center gap-1.5 rounded-full bg-[#2878f5] px-3 text-[10px] font-semibold text-white shadow-sm"><CalendarDays size={13} aria-hidden />查看行程</a>
      </header>
      {conversationNotice && <p role="status" className="mx-auto mt-2 w-full max-w-[940px] rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">{conversationNotice}</p>}
      <div className="mx-auto flex min-h-0 w-full max-w-[940px] flex-1 flex-col px-3 py-3 md:px-0 md:py-0">
        <TravelV2TripConversation
          key={`${trip.trip_id}:${conversationId}`}
          trip={trip}
          conversationId={conversationId}
          conversation={{ messages, setMessages, syncError }}
          onTripSaved={refreshAfterAIEdit}
          onRefreshTrip={refreshAssociatedTrip}
          onRestoreRequest={(revision) => { window.location.assign(travelV2WorkspaceRestoreHref(trip.trip_id, conversationId, revision)) }}
        />
      </div>
      <TravelBottomNav active="home" />
    </main>
  }

  return (
    <main className="flex min-h-screen flex-col bg-[#f4f8fd] pb-[68px] text-[#17283f] md:h-screen md:min-h-0 md:pb-0">
      <div className="hidden md:block"><TravelTopBar /></div>
      <header className="sticky top-0 z-20 flex h-[58px] items-center justify-between border-b border-[#e6edf5] bg-white px-4 md:mx-auto md:mb-3 md:h-[64px] md:w-full md:max-w-[940px] md:rounded-2xl md:border md:px-5">
        <a href="/travel" aria-label="返回旅游首页" className="flex h-9 w-9 items-center justify-center rounded-full text-[#536984] hover:bg-[#f3f7fc]"><ArrowLeft size={18} aria-hidden /></a>
        <div className="text-center"><p className="text-sm font-semibold text-[#263c57]">AI 旅行助手</p><p className="mt-0.5 text-[9px] text-[#94a2b3]">灵感、路线和行程调整</p></div>
        <a href="/travel/itineraries" className="inline-flex h-9 items-center gap-1.5 rounded-full border border-[#e3ebf5] px-3 text-[10px] font-medium text-[#6e8097]"><CalendarDays size={13} aria-hidden />我的行程</a>
      </header>
      <div className="mx-auto flex min-h-0 w-full max-w-[940px] flex-1 flex-col px-3 py-3 md:px-0 md:py-0">
        <section aria-label="旅行对话" className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[22px] border border-[#e4ebf4] bg-white shadow-[0_10px_34px_rgba(31,74,130,.06)] md:rounded-[24px]">
          <div className="flex-1 space-y-4 overflow-y-auto p-4 md:p-6" aria-live="polite">
            {orderedMessages.length === 0 && <div className="flex items-start gap-3"><span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-[#eaf2ff] text-[#2878f5]"><Sparkles size={16} aria-hidden /></span><div className="max-w-[88%] rounded-2xl rounded-tl-md bg-[#f2f7fd] px-4 py-3 text-xs leading-6 text-[#405875] md:max-w-[76%] md:text-sm">你好，我可以和你一起规划旅行。告诉我目的地、同行人数、预算和喜欢的节奏；也可以直接问景点、天气或交通。<div className="mt-3 flex flex-wrap gap-2">{SUGGESTIONS.map((suggestion) => <button key={suggestion} type="button" onClick={() => setDraft(suggestion)} className="rounded-full border border-[#dce8f7] bg-white px-3 py-1.5 text-[10px] text-[#587494] transition hover:border-[#9cc2f4] hover:text-[#2878f5]">{suggestion}</button>)}</div></div></div>}
            {orderedMessages.map((message) => <Fragment key={message.id}><div className={`flex items-start gap-3 ${message.role === 'user' ? 'justify-end' : ''}`}>{message.role === 'assistant' && <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-[#eaf2ff] text-[#2878f5]"><Sparkles size={16} aria-hidden /></span>}<div className={`max-w-[88%] whitespace-pre-wrap rounded-2xl px-4 py-3 text-xs leading-6 md:max-w-[76%] md:text-sm ${message.role === 'user' ? 'rounded-tr-md bg-[#2878f5] text-white' : 'rounded-tl-md bg-[#f2f7fd] text-[#405875]'}`}>{message.role === 'assistant' && message.tag && <span className={`mb-1 block text-[9px] font-semibold ${message.tone === 'warn' ? 'text-amber-700' : 'text-[#4882ca]'}`}>{message.tag}</span>}{visibleTravelMessage(message.text)}</div></div>{message.role === 'assistant' && cardsForTurn(message.turnId).length > 0 && <div className="pl-11"><ToolCardBlock cards={cardsForTurn(message.turnId)} onAsk={(text) => void sendText(text)} /></div>}</Fragment>)}
            {loading && cardsForTurn(process?.runId ?? '').length > 0 && <div className="pl-11"><ToolCardBlock cards={cardsForTurn(process?.runId ?? '')} onAsk={(text) => void sendText(text)} /></div>}
            {clarifyOptions && !loading && <div className="flex flex-wrap gap-2 pl-11" aria-label="槽位澄清选项">{clarifyOptions.options.map((option) => <button key={option.label} type="button" disabled={loading} onClick={() => void sendText(option.label)} className="rounded-full border border-[#a9c9f7] bg-[#eaf2ff] px-3 py-1.5 text-[11px] font-medium text-[#1f62c9] transition hover:bg-[#d9e9ff] disabled:opacity-50">{option.label}</button>)}</div>}
            {loading && <div className="flex items-start gap-3"><span className="mt-0.5 flex h-8 w-8 items-center justify-center rounded-xl bg-[#eaf2ff] text-[#2878f5]"><Sparkles size={16} aria-hidden /></span><div className="max-w-[88%] rounded-2xl rounded-tl-md bg-[#f2f7fd] px-4 py-3 text-xs text-[#526984]"><div className="flex items-center gap-2"><LoaderCircle size={14} className="animate-spin text-[#2878f5]" aria-hidden />{processLabel || '正在连接旅游 Agent…'}</div>{process && Object.keys(process.stages).length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{Object.entries(process.stages).map(([name, stage]) => <span key={name} className={`rounded-full px-2 py-1 text-[9px] ${stage.status === 'completed' ? 'bg-emerald-50 text-emerald-700' : stage.status === 'failed' ? 'bg-rose-50 text-rose-700' : 'bg-white text-[#6e8097]'}`}>{name}</span>)}</div>}{process && process.tools.length > 0 && <details open className="mt-2 rounded-xl border border-[#e4ebf3] bg-[#fbfdff] px-2.5 py-2 text-[9px] text-[#526984]"><summary className="cursor-pointer font-semibold">工具过程 · {process.tools.length} 项</summary><ul aria-label="Tool 与 Provider 状态" className="mt-1.5 space-y-1.5"><ToolProcessRows tools={process.tools.map((tool) => ({ ...tool, error: undefined }))} /></ul></details>}</div></div>}
            {draftPlan && <section aria-label="行程草稿预览" className="rounded-2xl border border-[#cfe0f5] bg-[#f6faff] p-3">
              <div className="flex items-center justify-between gap-2"><p className="text-xs font-semibold text-[#263c57]">草稿已生成 · 确认后保存为正式行程</p><span className="rounded-full bg-white px-2 py-0.5 text-[9px] font-medium text-[#6380a0]">草稿 · 未保存</span></div>
              <p className="mt-1 text-[10px] leading-4 text-[#8493a6]">{draftPlan.input.document.brief.destination} · {draftPlan.input.document.brief.day_count} 天 · {draftPlan.input.document.brief.travelers.adults + draftPlan.input.document.brief.travelers.children} 人 · 节奏 {draftPlan.input.document.brief.pace === 'relaxed' ? '轻松' : draftPlan.input.document.brief.pace === 'intense' ? '紧凑' : '均衡'}</p>
              <div className="mt-2 space-y-2">{draftPlan.input.document.days.map((day, index) => <div key={day.day_id} className="rounded-xl border border-[#e2ecf7] bg-white p-2.5"><p className="text-[11px] font-semibold text-[#2b405b]">第 {index + 1} 天 · {day.title || '行程安排'}</p><ul className="mt-1.5 space-y-1">{day.items.map((item) => <li key={item.item_id} className="flex items-baseline gap-2 text-[10px] leading-4 text-[#526984]"><span className="shrink-0 text-[#8190a3]">{item.start_time || '时间待定'}</span><span className="truncate">{item.title}</span><span className="shrink-0 text-[#93a0b1]">{item.duration_min} 分钟</span></li>)}</ul></div>)}</div>
              <div className="mt-3 flex flex-wrap gap-2">
                <button type="button" onClick={() => void saveDraftPlan()} disabled={writeFeedback?.status === 'VALIDATING' || writeFeedback?.status === 'SAVING' || writeFeedback?.status === 'UNKNOWN'} className="min-h-10 rounded-xl bg-[#2878f5] px-4 text-xs font-semibold text-white transition hover:bg-[#1769e9] disabled:cursor-not-allowed disabled:opacity-60">保存为正式行程</button>
                <button type="button" onClick={discardDraftPlan} disabled={writeFeedback?.status === 'VALIDATING' || writeFeedback?.status === 'SAVING'} className="min-h-10 rounded-xl border border-[#dce6f1] bg-white px-4 text-xs font-medium text-[#5b718d] transition hover:border-[#b9d3fa] hover:text-[#2878f5] disabled:opacity-60">继续调整</button>
              </div>
            </section>}
            {error && <div role="alert" className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5 text-[10px] leading-5 text-rose-800"><CircleAlert size={14} className="mt-0.5 shrink-0" aria-hidden />{error}</div>}
            {writeFeedback && <div role={travelWriteStatusRole(writeFeedback.status)} className={`rounded-xl border px-3 py-2.5 text-sm leading-5 ${travelWriteStatusTone(writeFeedback.status)}`}>
              <p><span className="mr-1.5 font-semibold">{travelWriteStatusLabel(writeFeedback.status)}</span>{writeFeedback.message}</p>
              {writeFeedback.status === 'UNKNOWN' && pendingPlanWriteRef.current && <button type="button" onClick={() => void retryPendingPlanWrite()} className="mt-2 min-h-10 rounded-lg border border-amber-300 bg-white px-3 text-sm font-semibold text-amber-950">使用同一请求安全重试</button>}
            </div>}
            {syncError && <p role="status" className="text-[10px] text-amber-700">{syncError}</p>}
            {conversationNotice && <p role="status" className="text-[10px] text-amber-700">{conversationNotice}</p>}
            {tripLoadNotice && <p role="status" className="text-[10px] text-amber-700">{tripLoadNotice}</p>}
            {orderedMessages.length > 0 && <div className="flex items-center gap-2 text-[10px] text-[#9aa8b8]"><MapPin size={12} aria-hidden />对话消息会按账号和行程保存；服务端行程状态以行程详情为准。</div>}
          </div>
          <form onSubmit={submit} className="shrink-0 border-t border-[#edf1f6] p-3 md:p-4"><label className="sr-only" htmlFor="travel-chat-input">输入旅行需求</label><div className="flex items-end gap-2 rounded-2xl border border-[#dfe8f3] bg-[#fbfdff] p-2 focus-within:border-[#99bdfa] focus-within:ring-4 focus-within:ring-[#2878f5]/[.08]"><textarea id="travel-chat-input" aria-label="输入旅行需求" rows={2} value={draft} onChange={(event) => setDraft(event.target.value)} disabled={loading || !tripAssociationReady || !conversationReady || writeFeedback?.status === 'UNKNOWN'} placeholder="接着提问，或告诉我你想怎么调整…" className="max-h-32 min-h-11 flex-1 resize-none bg-transparent px-2 py-2 text-xs leading-5 text-[#344b67] outline-none placeholder:text-[#9aa8b8] disabled:opacity-60 md:text-sm" /><button type="submit" aria-label="发送消息" disabled={!draft.trim() || loading || !tripAssociationReady || !conversationReady || writeFeedback?.status === 'UNKNOWN'} className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#2878f5] text-white transition hover:bg-[#1769e9] disabled:cursor-not-allowed disabled:bg-[#c7d7ed]"><Send size={16} aria-hidden /></button></div><div className="mt-2 flex items-center justify-between px-1"><span className="text-[9px] text-[#9aa8b8]">消息提交至旅游 Agent · 新行程仅在服务端确认后创建</span><a href="/travel/itineraries" className="inline-flex items-center gap-1 text-[10px] font-medium text-[#2878f5]">浏览历史行程 <ArrowRight size={12} aria-hidden /></a></div></form>
        </section>
      </div>
      <TravelBottomNav active="home" />
    </main>
  )
}
