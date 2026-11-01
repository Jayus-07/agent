'use client'

/**
 * useTravelPlanGeneration — V2 生成流引擎（P4 从 TravelChatPage 抽出复用）
 *
 * 职责：无正式行程时的「对话伴随生成」全流程——streamTravelPlanV2 SSE 消费
 * （过程行/tool.card/澄清选项）、草稿预览（A5 两段式）、确认保存
 * （createTravelTripV2 幂等 + UNKNOWN 同请求安全重试）、草稿撤下。
 *
 * 红线（迁移自 TravelChatPage 原实现，逻辑零改动，由其既有测试作回归网）：
 *  - 写状态机 travelWriteStatus 五态与幂等键（UNKNOWN 只允许同一请求重试）；
 *  - SSE 事件消费顺序与 process 归并（travelRuntime）；
 *  - 会话关联：保存成功后服务端会话为权威（conversationChanged 时切换并披露）。
 * 消费方：TravelChatPage（过渡期）与工作台生成态（P4a /travel/itineraries/new）。
 */
import { useCallback, useRef, useState } from 'react'
import { nanoid } from 'nanoid'
import { streamTravelPlanV2, parseTravelToolCard, type TravelClarificationOption, type TravelStreamEvent, type TravelToolCard } from '@/api/travel'
import {
  createTravelV2IdempotencyKey,
  createTravelTripV2,
  type CreateTravelTripV2,
  type TravelTripV2,
} from '@/api/travelV2'
import { initialTravelProcess, reduceTravelStreamEvent, travelProcessStatusLabel, type TravelProcessState } from '@/components/travel/travelRuntime'
import { useTravelConversation, type TravelConversationMessage } from '@/components/travel/useTravelConversation'
import { resolveTravelStreamOutcome } from './travelLiveRuntime'
import { itineraryToTripDocumentV2 } from './travelV2PlannerAdapter'
import {
  classifyTravelWriteError,
  isConfirmedCreatedTravelTrip,
  nextPaint,
  travelWriteErrorMessage,
  type TravelWriteStatus,
} from './travelV2WriteState'

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

export function visibleTravelMessage(text: string): string {
  return text.replace(DECISION_TOKEN_MARKER, '')
}

type PendingTravelPlanWrite = { key: string; input: CreateTravelTripV2; runId: string; replyText: string }

/** 原型 A5 语义：SSE 完成后先出逐日草稿预览，用户确认才落正式行程（v1）。 */
export type DraftPlanPreview = { runId: string; replyText: string; input: CreateTravelTripV2 }

export interface TravelPlanGenerationOptions {
  conversationId: string
  /** useTravelConversation 的最新消息（决策令牌从最新助手消息提取，同原实现）。 */
  messages: TravelConversationMessage[]
  setMessages: (updater: (current: TravelConversationMessage[]) => TravelConversationMessage[]) => void
  /** 生成入口门禁（会话就绪前禁止发送）。 */
  canSend: boolean
  /** 初始输入（首页携带的 prompt，原 ChatPage initialPrompt 语义）。 */
  initialDraft?: string
  /** 保存成功回调（服务端确认后；消费方负责切编辑态/披露时区警告）。 */
  onTripCreated: (savedTrip: TravelTripV2, context: { conversationChanged: boolean; resolvedConversationId: string }) => void
}

export function useTravelPlanGeneration({ conversationId, messages, setMessages, canSend, initialDraft = '', onTripCreated }: TravelPlanGenerationOptions) {
  const [draft, setDraft] = useState(initialDraft)
  const [loading, setLoading] = useState(false)
  const [process, setProcess] = useState<TravelProcessState | null>(null)
  const [error, setError] = useState('')
  const [draftPlan, setDraftPlan] = useState<DraftPlanPreview | null>(null)
  const [clarifyOptions, setClarifyOptions] = useState<{ runId: string; options: TravelClarificationOption[] } | null>(null)
  // F18：每轮对话的查询结果卡（tool.card 事件；SSE 会话内瞬时，历史消息不带卡）
  const [turnCards, setTurnCards] = useState<{ runId: string; cards: TravelToolCard[] }[]>([])
  const pendingPlanWriteRef = useRef<PendingTravelPlanWrite | null>(null)
  const planWriteLockRef = useRef(false)
  const [writeFeedback, setWriteFeedback] = useState<{ status: TravelWriteStatus; message: string } | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const cardsForTurn = useCallback(
    (turnId: string) => turnCards.find((entry) => entry.runId === turnId)?.cards ?? [],
    [turnCards],
  )

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

      pendingPlanWriteRef.current = null
      const resolvedConversationId = savedTrip.conversation_id || conversationId
      const conversationChanged = resolvedConversationId !== conversationId
      onTripCreated(savedTrip, { conversationChanged, resolvedConversationId })
      setError('')
      setWriteFeedback({ status: 'SAVED', message: `服务端已确认保存到 v${savedTrip.revision}.` })
      if (!conversationChanged) {
        setMessages((current) => [...current, {
          id: `${intent.runId}:assistant`, conversationId, turnId: intent.runId,
          role: 'assistant', text: `${intent.replyText}\n\n行程已保存到 V2 · v${savedTrip.revision}。`,
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
  }, [conversationId, onTripCreated, setMessages])

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
    if (!text || loading || !canSend || pendingPlanWriteRef.current) return
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
        text: `这次请求没有完成：${message}`,
        tag: '请求失败', tone: 'warn',
      }])
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      setLoading(false)
    }
  }, [canSend, conversationId, loading, messages, setMessages])

  return {
    draft, setDraft,
    loading, process, processLabel: process ? travelProcessStatusLabel({ loading, stopped: false, status: process.status }) : '',
    error,
    draftPlan,
    clarifyOptions,
    cardsForTurn,
    sendText,
    saveDraftPlan,
    discardDraftPlan,
    writeFeedback,
    retryPendingPlanWrite,
    pendingPlanWriteRef,
  }
}
