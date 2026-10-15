'use client'

import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type FormEvent, type SetStateAction } from 'react'
import { nanoid } from 'nanoid'
import { LoaderCircle, MessageCircle, RotateCw, Send, Sparkles } from 'lucide-react'
import { ApiError } from '@/api/client'
import {
  applyTravelTripAIEditV2,
  cancelTravelTripAIPendingResolutionV2,
  createTravelV2IdempotencyKey,
  fetchTravelTripAIPendingResolutionV2,
  fetchTravelTripAIEditStatusV2,
  fetchTravelTripV2,
  resolveTravelTripAIPendingResolutionV2,
  type TravelAIEditResponseV2,
  type TravelAIEditStatusV2,
  type TravelTripV2,
} from '@/api/travelV2'
import { streamTravelPlanV2, type TravelStreamEvent } from '@/api/travel'
import ToolProcessRows from '@/components/travel/ToolProcessRows'
import { initialTravelProcess, reduceTravelStreamEvent, travelProcessStatusLabel, type TravelProcessState } from '@/components/travel/travelRuntime'
import type { TravelConversationMessage } from '@/components/travel/useTravelConversation'
import { resolveTravelStreamOutcome } from './travelLiveRuntime'
import {
  buildTravelV2TripQuestionPrompt,
  parseTravelV2RestoreRevisionRequest,
} from './travelV2Conversation'

const OPERATION_MARKER = /\n\[\[travel-v2-ai-edit:([^:\]]+):(\d+)\]\]$/
const RESULT_MARKER = /\n\[\[travel-v2-ai-result:([^:\]]+):(read_only|clarify|unsupported|processing|unknown|pending_resolution|succeeded|failed|cancelled|expired|consumed)\]\]$/
const RESTORE_MARKER = /\n\[\[travel-v2-restore:(\d+)\]\]$/
const TERMINAL_STATUSES = new Set<TravelAIEditStatusV2>([
  'read_only', 'clarify', 'unsupported', 'succeeded', 'failed', 'cancelled', 'expired', 'consumed',
])
const POLL_INTERVAL_MS = 1200
const POLL_ATTEMPTS = 6

interface ConversationState {
  messages: TravelConversationMessage[]
  setMessages: Dispatch<SetStateAction<TravelConversationMessage[]>>
  syncError: string
}

interface OperationIntent {
  key: string
  expectedRevision: number
  expectedResultRevision?: number
  userText: string
  turnId: string
}

interface OperationView {
  intent: OperationIntent
  status: TravelAIEditStatusV2
  pending?: TravelAIEditResponseV2['pending_resolution']
  conflictMessage?: string
  detail: string
}

export default function TravelV2TripConversation({
  trip,
  conversationId,
  conversation,
  onTripSaved,
  onRefreshTrip,
  onRestoreRequest,
  embedded = false,
}: {
  trip: TravelTripV2
  conversationId: string
  conversation: ConversationState
  onTripSaved: (savedRevision: number) => Promise<void> | void
  onRefreshTrip?: () => Promise<void> | void
  onRestoreRequest?: (revision: number) => void
  embedded?: boolean
}) {
  const { messages, setMessages, syncError } = conversation
  const [draft, setDraft] = useState('')
  const [working, setWorking] = useState(false)
  const [operation, setOperation] = useState<OperationView | null>(null)
  const [process, setProcess] = useState<TravelProcessState | null>(null)
  const [refreshPending, setRefreshPending] = useState<{ intent: OperationIntent; revision: number } | null>(null)
  const [refreshingTrip, setRefreshingTrip] = useState(false)
  const operationRef = useRef<OperationView | null>(null)
  const locallyStartedOperationKeysRef = useRef(new Set<string>())
  const busyRef = useRef(false)
  const mountedRef = useRef(false)
  const abortRef = useRef<AbortController | null>(null)
  const tripRef = useRef(trip)
  tripRef.current = trip

  const orderedMessages = useMemo(() => messages, [messages])

  const updateOperation = useCallback((next: OperationView | null) => {
    operationRef.current = next
    setOperation(next)
  }, [])

  const setAssistantResult = useCallback((
    intent: OperationIntent,
    status: TravelAIEditStatusV2,
    text: string,
    tag: string,
    tone: TravelConversationMessage['tone'] = 'ok',
  ) => {
    const cleanText = `${text}\n[[travel-v2-ai-result:${intent.key}:${status}]]`
    setMessages((current) => {
      const existingIndex = current.findIndex((message) => {
        if (message.role !== 'assistant') return false
        return message.text.match(RESULT_MARKER)?.[1] === intent.key
      })
      const assistant: TravelConversationMessage = {
        id: `${intent.turnId}:assistant`, conversationId, turnId: intent.turnId,
        role: 'assistant', text: cleanText, tag, tone,
      }
      if (existingIndex < 0) return [...current, assistant]
      return current.map((message, index) => index === existingIndex ? assistant : message)
    })
  }, [conversationId, setMessages])

  const showUncertain = useCallback((intent: OperationIntent, status: 'processing' | 'unknown', detail: string) => {
    setAssistantResult(
      intent, status,
      status === 'processing'
        ? '服务端仍在处理这项操作。页面会继续查询状态；行程尚未确认保存。'
        : '结果待核实。系统会按原操作键查询服务端账本，不会重新生成或推测已经保存。',
      status === 'processing' ? '服务端处理中' : '结果待核实', 'warn',
    )
    updateOperation({ intent, status, detail })
  }, [setAssistantResult, updateOperation])

  const answerReadOnly = useCallback(async (intent: OperationIntent) => {
    const controller = new AbortController()
    abortRef.current = controller
    setAssistantResult(intent, 'processing', '服务端已确认这是只读咨询，正在结合当前正式行程生成答复。', '行程咨询')
    setOperation((current) => current?.intent.key === intent.key
      ? { ...current, status: 'processing', detail: '只读咨询' }
      : current)
    setProcess(initialTravelProcess(intent.turnId))
    const events: TravelStreamEvent[] = []
    try {
      for await (const event of streamTravelPlanV2(
        buildTravelV2TripQuestionPrompt(intent.userText, tripRef.current), conversationId,
        { signal: controller.signal, clientRunId: intent.turnId, mode: 'chat', source: 'manual' },
      )) {
        events.push(event)
        if (mountedRef.current) setProcess((current) => current ? reduceTravelStreamEvent(current, event) : current)
      }
      const outcome = resolveTravelStreamOutcome(events)
      if (outcome.kind === 'failed') throw new Error(outcome.message)
      const answer = outcome.response.final_answer?.trim()
        || '当前行程没有变化。你可以继续询问某一天的安排，或明确提出一项修改。'
      if (mountedRef.current) {
        setProcess((current) => current ? { ...current, status: 'completed' } : current)
        setAssistantResult(intent, 'read_only', answer, '行程咨询')
        updateOperation(null)
      }
    } catch {
      if (controller.signal.aborted || !mountedRef.current) return
      setProcess((current) => current ? { ...current, status: 'error', error: '旅游问答暂不可用' } : current)
      setAssistantResult(intent, 'read_only', '旅游问答暂时不可用。服务端已将本次请求判定为只读，正式行程没有变化。', '行程咨询暂不可用', 'warn')
      updateOperation(null)
    } finally {
      if (abortRef.current === controller) abortRef.current = null
    }
  }, [conversationId, setAssistantResult, updateOperation])

  const refreshOfficialTrip = useCallback(async (intent: OperationIntent, revision: number) => {
    try {
      const latest = await fetchTravelTripV2(tripRef.current.trip_id)
      if (latest.trip_id !== tripRef.current.trip_id || latest.revision < revision) {
        throw new Error('正式行程尚未返回已确认的 Revision。')
      }
      await onTripSaved(revision)
      setRefreshPending(null)
      setAssistantResult(
        intent, 'succeeded',
        `服务端操作账本已确认保存到 v${revision}，正式行程和版本历史已刷新。`,
        '已保存',
      )
    } catch {
      setRefreshPending({ intent, revision })
      setAssistantResult(
        intent, 'succeeded',
        `服务端操作账本已确认保存到 v${revision}；行程刷新暂未完成，请点击“重新核对正式行程”。`,
        '已保存，等待刷新', 'warn',
      )
    }
    updateOperation(null)
  }, [onTripSaved, setAssistantResult, updateOperation])

  const retryTripRefresh = useCallback(async () => {
    if (!refreshPending || refreshingTrip) return
    setRefreshingTrip(true)
    try {
      await refreshOfficialTrip(refreshPending.intent, refreshPending.revision)
    } finally {
      setRefreshingTrip(false)
    }
  }, [refreshPending, refreshingTrip, refreshOfficialTrip])

  const loadPending = useCallback(async (intent: OperationIntent, response: TravelAIEditResponseV2) => {
    if (response.trip_id !== tripRef.current.trip_id || response.operation_key !== intent.key
      || response.base_revision !== intent.expectedRevision || !response.pending_id) {
      showUncertain(intent, 'unknown', '待解决操作回执身份不匹配。')
      return
    }
    try {
      const authoritative = await fetchTravelTripAIPendingResolutionV2(
        tripRef.current.trip_id, response.pending_id,
      )
      const pending = authoritative.pending_resolution
      if (authoritative.trip_id !== tripRef.current.trip_id
        || authoritative.operation_key !== intent.key
        || authoritative.base_revision !== intent.expectedRevision
        || authoritative.pending_id !== response.pending_id
        || !pending || pending.pending_id !== response.pending_id) {
        showUncertain(intent, 'unknown', '无法核对服务端待解决记录。')
        return
      }
      const pendingStatus = String(pending.status)
      if (authoritative.status === 'expired' || pendingStatus === 'expired') {
        const detail = '待解决操作已过期；正式行程没有改变。'
        setAssistantResult(intent, 'expired', detail, '待解决操作已过期', 'warn')
        updateOperation(null)
        return
      }
      if (authoritative.status === 'cancelled' || pendingStatus === 'cancelled') {
        const detail = '待解决操作已取消；正式行程没有改变。'
        setAssistantResult(intent, 'cancelled', detail, '已取消', 'warn')
        updateOperation(null)
        return
      }
      if (authoritative.status === 'succeeded' || authoritative.status === 'consumed'
        || pendingStatus === 'consumed') {
        showUncertain(intent, 'unknown', '待解决记录已被服务端消费，正在核对原操作账本。')
        return
      }
      if (authoritative.status === 'failed' || pendingStatus === 'failed' || pendingStatus === 'blocked') {
        const conflictMessage = authoritative.conflicts?.map((conflict) => conflict.message).filter(Boolean).join('；')
        const detail = [authoritative.message || '服务端重新校验未通过，行程没有保存。', conflictMessage].filter(Boolean).join(' ')
        setAssistantResult(intent, 'failed', detail, '未保存', 'warn')
        updateOperation(null)
        return
      }
      if (authoritative.status !== 'pending_resolution' || pendingStatus !== 'pending') {
        showUncertain(intent, 'unknown', '待解决记录不是可确认状态；请查询原操作账本。')
        return
      }
      const status: TravelAIEditStatusV2 = 'pending_resolution'
      const conflictMessage = authoritative.conflicts?.map((conflict) => conflict.message).filter(Boolean).join('；')
      const detail = pending.allowed_resolutions.length
        ? '服务端提供了解决选项；只有你明确确认后才会提交。'
        : '服务端没有提供可确认的解决选项，当前操作保持阻塞。'
      setAssistantResult(intent, status, detail, '需要你确认', 'warn')
      updateOperation({ intent, status, pending, conflictMessage, detail })
    } catch {
      showUncertain(intent, 'unknown', '待解决操作读取失败。')
    }
  }, [setAssistantResult, showUncertain, updateOperation])

  const handleAuthoritativeStatus = useCallback(async (
    intent: OperationIntent,
    response: TravelAIEditResponseV2,
    allowPolling: boolean,
  ): Promise<boolean> => {
    if (response.trip_id !== tripRef.current.trip_id || response.operation_key !== intent.key
      || response.base_revision !== intent.expectedRevision) {
      showUncertain(intent, 'unknown', '操作状态与本次 Trip 或 Revision 不匹配。')
      return false
    }
    if (response.status === 'succeeded') {
      const revision = response.new_revision
      if (response.saved !== true || !Number.isInteger(revision) || (revision ?? 0) <= intent.expectedRevision) {
        showUncertain(intent, 'unknown', '成功状态缺少匹配的正式 Revision。')
        return false
      }
      if (intent.expectedResultRevision !== undefined && revision !== intent.expectedResultRevision) {
        showUncertain(intent, 'unknown', '提交回执与权威操作账本中的 Revision 不一致。')
        return false
      }
      await refreshOfficialTrip(intent, revision!)
      return true
    }
    if (response.status === 'pending_resolution') {
      await loadPending(intent, response)
      return false
    }
    if (response.status === 'read_only') {
      await answerReadOnly(intent)
      return false
    }
    if (response.status === 'clarify') {
      setAssistantResult(intent, 'clarify', response.clarification || '请确认是否要立即修改行程，并说明具体改动。', '需要补充信息', 'warn')
      updateOperation(null)
      return true
    }
    if (response.status === 'unsupported') {
      setAssistantResult(intent, 'unsupported', `${response.message || '当前入口只支持单项局部修改。'} 请返回规划入口创建新的旅行方案；当前行程没有变化。`, '当前无法安全完成', 'warn')
      updateOperation(null)
      return true
    }
    if (response.status === 'failed' || response.status === 'cancelled' || response.status === 'expired' || response.status === 'consumed') {
      const message = response.code === 'VERSION_CONFLICT'
        ? '行程版本已变化，本次修改没有自动重放。请刷新正式行程后重新提出修改。'
        : response.message || '服务端没有保存这项修改。'
      const conflictDetail = response.conflicts?.map((conflict) => {
        const names = affectedItemNames(conflict.affected_item_ids, tripRef.current)
        return `${conflict.message}${conflict.affected_item_ids.length ? `（影响：${names}）` : ''}`
      }).filter(Boolean).join('；')
      setAssistantResult(intent, response.status, [message, conflictDetail].filter(Boolean).join(' '), response.code === 'VERSION_CONFLICT' ? '版本冲突' : '未保存', 'warn')
      updateOperation(null)
      if (response.code === 'VERSION_CONFLICT') await onRefreshTrip?.()
      return true
    }
    if (response.status === 'processing' || response.status === 'unknown') {
      showUncertain(intent, response.status, response.message || '')
      return false
    }
    if (allowPolling) showUncertain(intent, 'unknown', '服务端状态尚未进入可确认终态。')
    return false
  }, [answerReadOnly, loadPending, onRefreshTrip, refreshOfficialTrip, setAssistantResult, showUncertain, updateOperation])

  const queryOperation = useCallback(async (intent: OperationIntent, attempts = POLL_ATTEMPTS) => {
    for (let index = 0; index < attempts && mountedRef.current; index += 1) {
      try {
        const result = await fetchTravelTripAIEditStatusV2(tripRef.current.trip_id, intent.key)
        const terminal = await handleAuthoritativeStatus(intent, result, false)
        if (terminal || result.status === 'pending_resolution' || result.status === 'read_only'
          || result.status === 'clarify' || result.status === 'unsupported'
          || result.status === 'failed' || result.status === 'cancelled' || result.status === 'expired') return
        if (index + 1 < attempts) await new Promise((resolve) => window.setTimeout(resolve, POLL_INTERVAL_MS))
      } catch (cause) {
        if (cause instanceof ApiError && cause.status === 404 && index === 0) {
          try {
            const replay = await applyTravelTripAIEditV2(tripRef.current.trip_id, {
              expected_revision: intent.expectedRevision,
              user_message: intent.userText,
              conversation_id: conversationId,
            }, intent.key)
            const terminal = await handleAuthoritativeStatus(intent, replay, false)
            if (terminal || replay.status !== 'processing' && replay.status !== 'unknown'
              && replay.status !== 'saved' && replay.status !== 'succeeded') return
          } catch {
            showUncertain(intent, 'unknown', '原操作账本暂不可读取；可继续查询原操作键。')
            return
          }
        } else {
          showUncertain(intent, 'unknown', '原操作状态查询失败；可继续查询原操作键。')
          return
        }
      }
    }
    if (mountedRef.current && operationRef.current?.intent.key === intent.key) {
      showUncertain(intent, operationRef.current.status === 'processing' ? 'processing' : 'unknown', '服务端仍未给出终态。')
    }
  }, [conversationId, handleAuthoritativeStatus, showUncertain])

  const submitIntent = useCallback(async (intent: OperationIntent) => {
    if (busyRef.current || !mountedRef.current) return
    busyRef.current = true
    setWorking(true)
    updateOperation({ intent, status: 'processing', detail: '正在由服务端判断问答或修改权限。' })
    try {
      const result = await applyTravelTripAIEditV2(tripRef.current.trip_id, {
        expected_revision: intent.expectedRevision,
        user_message: intent.userText,
        conversation_id: conversationId,
      }, intent.key)
      if (result.trip_id !== tripRef.current.trip_id || result.operation_key !== intent.key
        || result.base_revision !== intent.expectedRevision) {
        showUncertain(intent, 'unknown', '操作回执身份不匹配。')
        return
      }
      if (result.status === 'read_only') {
        await answerReadOnly(intent)
        return
      }
      if (result.status === 'clarify' || result.status === 'unsupported') {
        await handleAuthoritativeStatus(intent, result, true)
        return
      }
      if (result.status === 'pending_resolution') {
        await loadPending(intent, result)
        return
      }
      if (result.status === 'failed' && result.code !== 'AI_EDIT_OUTCOME_UNKNOWN' && result.code !== 'AI_EDIT_CALL_UNKNOWN') {
        await handleAuthoritativeStatus(intent, result, true)
        return
      }
      if (result.status === 'saved' || result.status === 'succeeded') {
        if (result.saved !== true || result.new_revision !== intent.expectedRevision + 1) {
          showUncertain(intent, 'unknown', '提交回执没有提供匹配的 Revision。')
          return
        }
        intent.expectedResultRevision = result.new_revision
      }
      await queryOperation(intent)
    } catch {
      showUncertain(intent, 'unknown', '请求回执未能确认。系统将只查询原操作键，不会换键重试。')
      await queryOperation(intent)
    } finally {
      busyRef.current = false
      if (mountedRef.current) setWorking(false)
    }
  }, [answerReadOnly, conversationId, handleAuthoritativeStatus, loadPending, queryOperation, showUncertain, updateOperation])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      abortRef.current?.abort()
    }
  }, [])

  useEffect(() => {
    const intent = findUnresolvedOperation(messages)
    if (!intent || locallyStartedOperationKeysRef.current.has(intent.key)
      || operationRef.current?.intent.key === intent.key || busyRef.current) return
    locallyStartedOperationKeysRef.current.add(intent.key)
    updateOperation({ intent, status: 'unknown', detail: '刷新后正在核对原操作键。' })
    setWorking(true)
    void queryOperation(intent).finally(() => {
      if (mountedRef.current) setWorking(false)
    })
  }, [messages, queryOperation, updateOperation])

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const text = draft.trim()
    if (!text || busyRef.current || operationRef.current || refreshPending) return
    const turnId = `client-${Date.now()}-${nanoid(8)}`
    const restoreRevision = parseTravelV2RestoreRevisionRequest(text)
    if (restoreRevision !== null) {
      setMessages((current) => [...current,
        { id: `${turnId}:user`, conversationId, turnId, role: 'user', text },
        {
          id: `${turnId}:assistant`, conversationId, turnId, role: 'assistant',
          text: `已识别为恢复正式行程 v${restoreRevision}。请先核对版本内容，再在历史面板确认；确认后服务端会新增一个 Revision。\n[[travel-v2-restore:${restoreRevision}]]`,
          tag: '恢复版本 · 等待确认', tone: 'warn',
        },
      ])
      setDraft('')
      setProcess(null)
      return
    }
    const intent: OperationIntent = {
      key: createTravelV2IdempotencyKey(),
      expectedRevision: trip.revision,
      userText: text,
      turnId,
    }
    locallyStartedOperationKeysRef.current.add(intent.key)
    const storedUserText = `${text}\n[[travel-v2-ai-edit:${intent.key}:${intent.expectedRevision}]]`
    updateOperation({ intent, status: 'processing', detail: '正在由服务端判断问答或修改权限。' })
    setMessages((current) => [...current, {
      id: `${turnId}:user`, conversationId, turnId, role: 'user', text: storedUserText,
    }])
    setDraft('')
    setProcess(null)
    void submitIntent(intent)
  }

  async function confirmResolution(resolutionId: string) {
    const current = operationRef.current
    const pending = current?.pending
    if (!current || current.status !== 'pending_resolution' || !pending
      || !pending.allowed_resolutions.some((item) => item.resolution_id === resolutionId)
      || busyRef.current) return
    busyRef.current = true
    setWorking(true)
    updateOperation({ ...current, detail: '正在提交你明确确认的服务端解决选项。' })
    const stableResolutionKey = `travel-v2-resolution-${pending.pending_id}-${resolutionId}`
    try {
      await resolveTravelTripAIPendingResolutionV2(
        tripRef.current.trip_id, pending.pending_id, resolutionId, stableResolutionKey,
      )
      await queryOperation(current.intent)
    } catch {
      showUncertain(current.intent, 'unknown', '解决操作回执待核实；请继续查询原操作状态。')
      await queryOperation(current.intent)
    } finally {
      busyRef.current = false
      if (mountedRef.current) setWorking(false)
    }
  }

  async function cancelResolution() {
    const current = operationRef.current
    if (!current?.pending || current.status !== 'pending_resolution' || busyRef.current) return
    busyRef.current = true
    setWorking(true)
    try {
      const result = await cancelTravelTripAIPendingResolutionV2(
        tripRef.current.trip_id, current.pending.pending_id,
      )
      if (result.status === 'cancelled'
        && result.trip_id === tripRef.current.trip_id
        && result.pending_id === current.pending.pending_id
        && result.operation_key === current.intent.key) {
        setAssistantResult(current.intent, 'cancelled', '已取消待解决操作；正式行程没有变化。', '已取消', 'warn')
        updateOperation(null)
      } else {
        await queryOperation(current.intent, 1)
      }
    } catch {
      await queryOperation(current.intent, 1)
    } finally {
      busyRef.current = false
      if (mountedRef.current) setWorking(false)
    }
  }

  const visibleMessages = orderedMessages.map((message) => ({
    ...message,
    restoreRevision: message.role === 'assistant' ? Number(message.text.match(RESTORE_MARKER)?.[1]) || null : null,
    text: stripInternalMarkers(message.text),
  }))
  const progressLabel = process ? travelProcessStatusLabel({ loading: working, stopped: false, status: process.status }) : ''

  return <section aria-label="旅行对话" className={`flex min-h-0 flex-1 flex-col overflow-hidden bg-white ${embedded ? '' : 'rounded-[22px] border border-[#e4ebf4] shadow-[0_10px_34px_rgba(31,74,130,.06)]'}`}>
    <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3 md:p-4" aria-live="polite">
      {visibleMessages.length === 0 && <div className="flex items-start gap-2"><span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-[#eaf2ff] text-[#2878f5]"><Sparkles size={14} aria-hidden /></span><div className="rounded-xl rounded-tl-sm bg-[#f2f7fd] px-3 py-2.5 text-[11px] leading-5 text-[#405875]">你好，我已经打开这条正式行程。你可以询问安排，也可以明确提出一项修改。</div></div>}
      {visibleMessages.map((message) => <div key={message.id} className={`flex items-start gap-2 ${message.role === 'user' ? 'justify-end' : ''}`}>
        {message.role === 'assistant' && <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-[#eaf2ff] text-[#2878f5]"><Sparkles size={14} aria-hidden /></span>}
        <div className={`max-w-[92%] whitespace-pre-wrap rounded-xl px-3 py-2.5 text-[11px] leading-5 ${message.role === 'user' ? 'rounded-tr-sm bg-[#2878f5] text-white' : 'rounded-tl-sm bg-[#f2f7fd] text-[#405875]'}`}>
          {message.role === 'assistant' && message.tag && <span className="mb-1 block text-[9px] font-semibold text-[#4882ca]">{message.tag}</span>}{message.text}
          {message.restoreRevision !== null && onRestoreRequest && <button
            type="button"
            onClick={() => onRestoreRequest(message.restoreRevision!)}
            className="mt-2 min-h-9 rounded-lg border border-[#bfd3ec] bg-white px-3 font-semibold text-[#315f91]"
          >打开历史并确认恢复 v{message.restoreRevision}</button>}
        </div>
      </div>)}
      {working && <div className="flex items-start gap-2"><span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-[#eaf2ff] text-[#2878f5]"><Sparkles size={14} aria-hidden /></span><div className="rounded-xl bg-[#f2f7fd] px-3 py-2.5 text-[10px] text-[#526984]"><div className="flex items-center gap-2"><LoaderCircle size={13} className="animate-spin text-[#2878f5]" aria-hidden />{progressLabel || operation?.detail || '正在核对服务端状态…'}</div></div></div>}
      {process && (Object.keys(process.stages).length > 0 || process.tools.length > 0) && <details open={working} className="ml-9 rounded-xl border border-[#e4ebf3] bg-[#fbfdff] px-3 py-2 text-[9px] text-[#526984]">
        <summary className="cursor-pointer font-semibold">本次处理过程 · {process.status === 'completed' ? '已完成' : process.status === 'error' ? '执行失败' : '进行中'}</summary>
        <div className="mt-2 space-y-2">
          {Object.keys(process.stages).length > 0 && <div className="flex flex-wrap gap-1">{Object.entries(process.stages).map(([name, stage]) => <span key={name} className={`rounded-full px-2 py-1 ${stage.status === 'completed' ? 'bg-emerald-50 text-emerald-700' : stage.status === 'failed' ? 'bg-rose-50 text-rose-700' : 'bg-white text-[#6e8097]'}`}>{name} · {stage.status === 'completed' ? '完成' : stage.status === 'failed' ? '失败' : '进行中'}</span>)}</div>}
          {process.tools.length > 0 && <ul aria-label="Tool 与 Provider 状态" className="space-y-1.5"><ToolProcessRows tools={process.tools.map((tool) => ({ ...tool, error: undefined }))} /></ul>}
        </div>
      </details>}
      {operation && operation.status === 'pending_resolution' && operation.pending && <div className="ml-9 rounded-xl border border-amber-200 bg-amber-50 p-3 text-[10px] leading-5 text-amber-950">
        <p className="font-semibold">行程存在安排冲突</p>
        <p>{operation.conflictMessage || operation.pending.conflict_codes.join('、') || '服务端检测到时间冲突'} · 影响 {affectedItemNames(operation.pending.affected_item_ids, trip)}</p>
        <p>有效期至 {new Date(operation.pending.expires_at).toLocaleString('zh-CN')}</p>
        {operation.pending.allowed_resolutions.length > 0
          ? <div className="mt-2 flex flex-wrap gap-2">{operation.pending.allowed_resolutions.map((resolution) => <button key={resolution.resolution_id} type="button" disabled={working} onClick={() => void confirmResolution(resolution.resolution_id)} className="min-h-9 rounded-lg bg-amber-900 px-3 text-[10px] font-semibold text-white disabled:opacity-50">确认：{resolution.label}</button>)}<button type="button" disabled={working} onClick={() => void cancelResolution()} className="min-h-9 rounded-lg border border-amber-300 bg-white px-3 text-[10px] font-semibold disabled:opacity-50">取消修改</button></div>
          : <><p className="mt-1">服务端没有提供可确认的解决选项，此操作保持阻塞。</p><button type="button" disabled={working} onClick={() => void cancelResolution()} className="mt-2 min-h-9 rounded-lg border border-amber-300 bg-white px-3 text-[10px] font-semibold disabled:opacity-50">取消修改</button></>}
      </div>}
      {operation && (operation.status === 'unknown' || operation.status === 'processing') && !working && <div className="ml-9 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-[10px] leading-5 text-amber-950"><p>{operation.detail || '操作状态尚待服务端确认。'}</p><button type="button" onClick={() => { setWorking(true); void queryOperation(operation.intent, 10).finally(() => { if (mountedRef.current) setWorking(false) }) }} className="mt-2 inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-amber-300 bg-white px-3 font-semibold"><RotateCw size={12} aria-hidden />继续查询原操作</button></div>}
      {refreshPending && <div role="status" className="ml-9 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-[10px] leading-5 text-amber-950"><p>服务端已确认保存到 v{refreshPending.revision}，但页面还没有核对最新行程和版本历史。重新读取不会再次提交修改。</p><button type="button" disabled={refreshingTrip} onClick={() => void retryTripRefresh()} className="mt-2 min-h-9 rounded-lg border border-amber-300 bg-white px-3 font-semibold disabled:opacity-50">{refreshingTrip ? '正在重新核对…' : '重新核对正式行程'}</button></div>}
      {syncError && <p role="status" className="rounded-lg bg-amber-50 px-3 py-2 text-[9px] leading-4 text-amber-800">{syncError}</p>}
    </div>
    <form onSubmit={submit} className="shrink-0 border-t border-[#edf1f6] p-2.5">
      <label htmlFor={`trip-conversation-${trip.trip_id}`} className="sr-only">与 AI 讨论或修改当前行程</label>
      <div className="flex items-end gap-2 rounded-xl border border-[#dfe8f3] bg-[#fbfdff] p-1.5">
      <textarea id={`trip-conversation-${trip.trip_id}`} rows={2} maxLength={4000} value={draft} onChange={(event) => setDraft(event.target.value)} disabled={working || Boolean(operation) || Boolean(refreshPending)} placeholder="询问行程，或提出一项修改…" className="min-h-10 flex-1 resize-none bg-transparent px-2 py-1 text-[11px] leading-5 text-[#344b67] outline-none placeholder:text-[#9aa8b8] disabled:opacity-60" />
        <button type="submit" aria-label="发送消息" disabled={working || Boolean(operation) || Boolean(refreshPending) || !draft.trim()} className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#2878f5] text-white disabled:opacity-50"><Send size={14} aria-hidden /></button>
      </div>
      <div className="mt-1.5 flex items-center justify-between px-1 text-[8px] text-[#9aa8b8]"><span className="inline-flex items-center gap-1"><MessageCircle size={10} aria-hidden />只修改服务端允许的单项内容</span><span>Trip v{trip.revision}</span></div>
    </form>
  </section>
}

function affectedItemNames(ids: string[], trip: TravelTripV2): string {
  if (!ids.length) return '行程整体'
  const names = new Map(trip.document.days.flatMap((day) => day.items.map((item) => [item.item_id, item.title] as const)))
  return ids.map((id) => names.get(id) || id).join('、')
}

function findUnresolvedOperation(messages: TravelConversationMessage[]): OperationIntent | null {
  const candidates = messages.flatMap((message) => {
    if (message.role !== 'user') return []
    const match = message.text.match(OPERATION_MARKER)
    if (!match) return []
    return [{
      key: match[1], expectedRevision: Number(match[2]),
      userText: message.text.replace(OPERATION_MARKER, ''), turnId: message.turnId,
    }]
  })
  const intent = candidates.at(-1)
  if (!intent) return null
  const result = messages.find((message) => message.role === 'assistant'
    && message.text.match(RESULT_MARKER)?.[1] === intent.key)
  const status = result?.text.match(RESULT_MARKER)?.[2] as TravelAIEditStatusV2 | undefined
  return status && TERMINAL_STATUSES.has(status) ? null : intent
}

function stripInternalMarkers(text: string): string {
  return text.replace(OPERATION_MARKER, '').replace(RESULT_MARKER, '').replace(RESTORE_MARKER, '')
}
