'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, CalendarDays, Check, ChevronDown, Clock3, History, MapPin, MessageCircle, MoreHorizontal, RotateCcw, Sparkles, Users, Wallet, X } from 'lucide-react'
import {
  addTravelMealV2,
  applyTravelTripEditV2,
  createTravelV2IdempotencyKey,
  fetchTravelTripRevisionsV2,
  fetchTravelTripV2,
  fetchTravelTripConversationV2,
  restoreTravelTripV2,
  searchTravelFoodV2,
  type MerchantSearchResultV2,
  type MerchantSnapshotV2,
  type TravelTripV2,
  type TripDayV2,
  type TripEditOperationV2,
  type TripItemV2,
} from '@/api/travelV2'
import TravelSearchActivity from './TravelSearchActivity'
import TravelSearchPanel, { type TravelSearchEvent, type TravelSearchKind } from './TravelSearchPanel'
import TravelV2Map from './TravelV2Map'
import type { TemporaryTravelMapLocation } from './travelV2MapData'
import TravelV2EditDialog, { type TravelV2EditPanel } from './TravelV2EditDialog'
import { paceLabel } from './travelOverview'
import TravelTripHistoryPanel from './TravelTripHistoryPanel'
import TravelV2TripConversation from './TravelV2TripConversation'
import AutoOptimizePanel from './AutoOptimizePanel'
import { useTravelConversation } from '@/components/travel/useTravelConversation'
import { defaultTravelV2ConversationId, setTravelV2TripAssociation, travelV2TripIdsMatch } from './travelV2Conversation'
import { TravelBottomNav } from './TravelV2Frame'
import {
  classifyTravelWriteError,
  isConfirmedTravelWrite,
  nextPaint,
  travelWriteErrorMessage,
  travelWriteStatusLabel,
  travelWriteStatusRole,
  travelWriteStatusTone,
  transitionTravelTripWrite,
  type PendingTravelTripWrite,
  type TravelTripWriteTransition,
  type TravelWriteStatus,
} from './travelV2WriteState'

type Tab = 'trip' | 'food' | 'hotel' | 'train'
const TABS: Array<{ id: Tab; label: string }> = [
  { id: 'trip', label: '行程' }, { id: 'food', label: '美食' }, { id: 'hotel', label: '酒店' }, { id: 'train', label: '车次' },
]

interface EditIntent {
  tripId: string
  expectedRevision: number
  operation: TripEditOperationV2
  summary: string
  key: string
  signature: string
  baseTrip: TravelTripV2
}

interface RestoreIntent {
  tripId: string
  expectedRevision: number
  targetRevision: number
  key: string
  signature: string
  baseTrip: TravelTripV2
}

interface WriteFeedback {
  status: TravelWriteStatus
  message: string
}

export default function TravelV2TripWorkspace(props: { tripId: string; editMode?: boolean }) {
  // 一个 Trip 一个组件实例，切换 route 时所有页面状态随旧实例卸载。
  // 写请求不会因此中止；迟到回执只能更新旧实例，不能进入新 Trip 页面。
  return <TravelV2TripWorkspaceInstance key={props.tripId} tripId={props.tripId} editMode={props.editMode} />
}

function TravelV2TripWorkspaceInstance({ tripId, editMode = false }: { tripId: string; editMode?: boolean }) {
  const [trip, setTrip] = useState<TravelTripV2 | null>(null)
  const [conversationId, setConversationId] = useState(() => defaultTravelV2ConversationId(tripId))
  const [conversationReady, setConversationReady] = useState(false)
  const [conversationNotice, setConversationNotice] = useState('')
  const conversation = useTravelConversation(conversationId, { enabled: conversationReady })
  const tripRef = useRef<TravelTripV2 | null>(null)
  tripRef.current = trip
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [activeTab, setActiveTab] = useState<Tab>('trip')
  const [dayIndex, setDayIndex] = useState(0)
  const [selectedItemId, setSelectedItemId] = useState('')
  const [selectedLocation, setSelectedLocation] = useState<TemporaryTravelMapLocation | null>(null)
  const [undoRevision, setUndoRevision] = useState<number | null>(null)
  const [undoing, setUndoing] = useState(false)
  const [savingMessage, setSavingMessage] = useState('')
  const [editPanel, setEditPanel] = useState<TravelV2EditPanel>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [restoreRequest, setRestoreRequest] = useState<{ revision: number } | null>(null)
  // F04 自动优选入口：建议确认卡（选入走既有单条 selections 端点逐条幂等）
  const [autoOptimizeOpen, setAutoOptimizeOpen] = useState(false)
  // F05 详情卡内联备选已收进编辑弹窗（ux2 #2/#3）：候选入口统一走三点菜单，
  // 左列地图以下不再挂候选/详情面板。
  // 时区提示可关闭（ux2 #1）：同行程记住关闭，不重复占屏。
  const [tzBannerDismissed, setTzBannerDismissed] = useState(false)
  const [writeFeedback, setWriteFeedback] = useState<WriteFeedback | null>(null)
  const [restoreFeedback, setRestoreFeedback] = useState<WriteFeedback | null>(null)
  const [refreshNotice, setRefreshNotice] = useState('')
  const [pendingTripWrite, setPendingTripWrite] = useState<PendingTravelTripWrite | null>(null)
  const pendingTripWriteRef = useRef<PendingTravelTripWrite | null>(null)
  const [refreshingLatest, setRefreshingLatest] = useState(false)
  const [editing, setEditing] = useState(false)
  const editingRef = useRef(false)
  const editIntentRef = useRef<EditIntent | null>(null)
  const restoreIntentRef = useRef<RestoreIntent | null>(null)
  const [searchEvents, setSearchEvents] = useState<TravelSearchEvent[]>([])
  const closeHistory = useCallback(() => {
    setHistoryOpen(false)
    setRestoreRequest(null)
  }, [])

  useEffect(() => {
    const targetRevision = Number(new URLSearchParams(window.location.search).get('restore_revision'))
    if (Number.isSafeInteger(targetRevision) && targetRevision > 0) {
      setRestoreRequest({ revision: targetRevision })
      setHistoryOpen(true)
    }
  }, [tripId])

  const refreshAfterAIEdit = useCallback(async (savedRevision: number) => {
    const [latest, history] = await Promise.all([
      fetchTravelTripV2(tripId), fetchTravelTripRevisionsV2(tripId),
    ])
    if (latest.trip_id !== tripId || latest.revision < savedRevision
      || !history.revisions.some((item) => item.revision === savedRevision)) {
      throw new Error('服务器尚未返回 AI 编辑对应的正式 Revision。')
    }
    setTrip((current) => current?.trip_id === tripId && current.revision > latest.revision ? current : latest)
    const currentRevision = history.revisions.find((item) => item.revision === latest.revision)
    setUndoRevision(currentRevision?.change_type === 'restore_revision' ? null : currentRevision?.parent_revision ?? null)
    setWriteFeedback({ status: 'SAVED', message: `AI 对话修改已保存，当前正式 Revision 为 v${latest.revision}。` })
    setRefreshNotice('')
  }, [tripId])

  const refreshAfterAIConflict = useCallback(async () => {
    const latest = await fetchTravelTripV2(tripId)
    if (latest.trip_id !== tripId) throw new Error('服务器返回了不匹配的行程。')
    setTrip((current) => current?.trip_id === tripId && current.revision > latest.revision ? current : latest)
    setUndoRevision(null)
  }, [tripId])

  function transitionTripWrite(event: TravelTripWriteTransition): boolean {
    if (event.status === 'VALIDATING') setRefreshNotice('')
    const transition = transitionTravelTripWrite(pendingTripWriteRef.current, event)
    if (!transition.accepted) return false
    pendingTripWriteRef.current = transition.current
    setPendingTripWrite(transition.current)
    return true
  }

  function selectWorkspaceTab(nextTab: Tab) {
    if (pendingTripWriteRef.current) return
    if (nextTab !== activeTab) setSelectedLocation(null)
    setActiveTab(nextTab)
  }

  const recordSearchEvent = useCallback((event: TravelSearchEvent) => {
    setSearchEvents((current) => {
      const existingIndex = current.findIndex((item) => item.queryId === event.queryId)
      if (existingIndex === -1) return [event, ...current].slice(0, 5)
      if (current[existingIndex].status !== 'querying') return current
      return current.map((item, index) => index === existingIndex ? event : item)
    })
  }, [])

  useEffect(() => {
    let cancelled = false
    const controller = new AbortController()
    setLoading(true)
    setError('')
    setConversationReady(false)
    setConversationNotice('')
    void fetchTravelTripV2(tripId, { signal: controller.signal }).then(async (result) => {
      if (cancelled) return
      let previousRevision: number | null = null
      try {
        const history = await fetchTravelTripRevisionsV2(tripId, { signal: controller.signal })
        const latest = history.revisions.find((entry) => entry.revision === result.revision)
        if (latest?.change_type !== 'restore_revision') previousRevision = latest?.parent_revision ?? null
      } catch {
        // 历史读取失败不影响正式行程加载，只隐藏撤销入口。
      }
      setTrip(result)
      setUndoRevision(previousRevision)
      try {
        const association = await fetchTravelTripConversationV2(tripId, { signal: controller.signal })
        if (cancelled) return
        if (!travelV2TripIdsMatch(association.trip_id, result.trip_id)) {
          throw new Error('服务端返回的行程会话关联与当前行程不匹配。')
        }
        if (!association.linked || !association.conversation_id?.trim()) {
          setConversationNotice('该行程没有可验证的服务端原始会话关联；未读取 URL 会话或任何对话历史。')
        } else {
          const candidate = new URLSearchParams(window.location.search).get('conversation_id') || ''
          const resolved = association.conversation_id
          if (candidate && candidate !== resolved) {
            setConversationNotice('链接中的会话与该行程的服务端关联不一致，已恢复行程原始会话。')
          }
          setConversationId(resolved)
          setTravelV2TripAssociation(resolved, result.trip_id)
          const url = new URL(window.location.href)
          if (url.searchParams.get('conversation_id') !== resolved) {
            url.searchParams.set('conversation_id', resolved)
            window.history.replaceState(window.history.state, '', url.toString())
          }
          setConversationReady(true)
        }
      } catch {
        if (!cancelled) {
          setConversationReady(false)
          setConversationNotice('行程已加载，但原始会话关联暂时无法验证；对话已停用，行程仍可查看和编辑。')
        }
      }
    }).catch((cause) => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : '行程暂时无法加载。')
    }).finally(() => { if (!cancelled) setLoading(false) })
    return () => {
      cancelled = true
      controller.abort()
    }
  }, [tripId])

  useEffect(() => {
    if (editPanel && !editIntentRef.current) setWriteFeedback(null)
  }, [editPanel])

  const days = trip?.document.days ?? []
  const day = days[dayIndex] ?? null
  const selectedItem = selectedLocation
    ? null
    : day?.items.find((item) => item.item_id === selectedItemId) ?? day?.items[0] ?? null
  const allMapItemIds = useMemo(() => new Set((day?.items ?? []).map((item) => item.item_id)), [day])

  function selectDay(index: number) {
    const nextDay = days[index]
    setDayIndex(index)
    setSelectedItemId(nextDay?.items[0]?.item_id ?? '')
    setSelectedLocation(null)
  }

  function saved(nextTrip: TravelTripV2, summary: string) {
    const current = tripRef.current
    if (!current || current.trip_id !== nextTrip.trip_id || nextTrip.revision <= current.revision) return
    setTrip(nextTrip)
    setUndoRevision(current.revision)
    setWriteFeedback({ status: 'SAVED', message: `${summary} 服务端已确认，当前 Revision 为 v${nextTrip.revision}。` })
    setSavingMessage('')
    setError('')
  }

  async function applyEdit(operation: TripEditOperationV2, summary: string): Promise<boolean> {
    if (!trip || editingRef.current) return false
    const signature = JSON.stringify({ tripId: trip.trip_id, expectedRevision: trip.revision, operation, summary })
    const existing = editIntentRef.current
    if (writeFeedback?.status === 'UNKNOWN' && existing && existing.signature !== signature) {
      setWriteFeedback({ status: 'UNKNOWN', message: '先使用同一请求安全重试，确认原操作结果后再提交新修改。' })
      return false
    }
    const intent: EditIntent = {
      tripId: trip.trip_id,
      expectedRevision: trip.revision,
      operation,
      summary,
      key: existing?.signature === signature ? existing.key : createTravelV2IdempotencyKey(),
      signature,
      baseTrip: trip,
    }
    return sendEdit(intent)
  }

  async function sendEdit(intent: EditIntent): Promise<boolean> {
    if (editingRef.current) return false
    const identity = { kind: 'edit' as const, tripId: intent.tripId, signature: intent.signature, key: intent.key }
    if (!transitionTripWrite({ ...identity, status: 'VALIDATING' })) return false
    editIntentRef.current = intent
    editingRef.current = true
    setEditing(true)
    setSavingMessage('')
    setWriteFeedback({ status: 'VALIDATING', message: '正在检查本次修改内容…' })
    await nextPaint()
    transitionTripWrite({ ...identity, status: 'SAVING' })
    setWriteFeedback({ status: 'SAVING', message: '正在请求服务端校验并提交正式行程…' })
    const previousItemIds = new Set(intent.baseTrip.document.days.flatMap((item) => item.items.map((entry) => entry.item_id)))
    try {
      const result = await applyTravelTripEditV2(intent.tripId, {
        expected_revision: intent.expectedRevision,
        change_summary: intent.summary,
        operation: intent.operation,
      }, intent.key)
      if (!isConfirmedTravelWrite(result, intent.tripId, intent.expectedRevision)) {
        transitionTripWrite({ ...identity, status: 'UNKNOWN' })
        setWriteFeedback({ status: 'UNKNOWN', message: '服务端返回尚不能证明事务提交成功；行程仍显示上次确认的版本。可用同一请求安全重试。' })
        return false
      }
      const nextTrip = { ...intent.baseTrip, revision: result.revision, document: result.document }
      const current = tripRef.current
      if (current?.trip_id === intent.tripId && current.revision === intent.expectedRevision) {
        setTrip(nextTrip)
        setUndoRevision(intent.expectedRevision)
      }
      const budgetWarning = result.document.health.issues.find((issue) => issue.code.startsWith('BUDGET_'))
      setWriteFeedback({
        status: 'SAVED',
        message: current && current.revision > result.revision
          ? `${intent.summary} 已由服务端确认保存到 v${result.revision}；页面已载入更新的 v${current.revision}，保留当前版本。${budgetWarning ? ` ${budgetWarning.message}` : ''}`
          : `${intent.summary} 已保存到 v${result.revision}。${budgetWarning ? ` ${budgetWarning.message}` : ''}`,
      })
      transitionTripWrite({ ...identity, status: 'SAVED' })
      if (current?.trip_id === intent.tripId && current.revision === intent.expectedRevision) {
        setSelectedLocation(null)
        const selectedExists = nextTrip.document.days.some((entry) => entry.items.some((stop) => stop.item_id === selectedItemId))
        const addedItem = nextTrip.document.days.flatMap((entry) => entry.items.map((stop) => ({ dayId: entry.day_id, stop }))).find(({ stop }) => !previousItemIds.has(stop.item_id))
        if (intent.operation.op === 'add_day') {
          setDayIndex(nextTrip.document.days.length - 1)
          setSelectedItemId('')
        } else if (addedItem) {
          const targetIndex = nextTrip.document.days.findIndex((entry) => entry.day_id === addedItem.dayId)
          setDayIndex(Math.max(0, targetIndex))
          setSelectedItemId(addedItem.stop.item_id)
        } else if (selectedExists) {
          const selectedDayIndex = nextTrip.document.days.findIndex((entry) => entry.items.some((stop) => stop.item_id === selectedItemId))
          if (selectedDayIndex >= 0) setDayIndex(selectedDayIndex)
        } else {
          const safeDayIndex = Math.min(dayIndex, Math.max(0, nextTrip.document.days.length - 1))
          setDayIndex(safeDayIndex)
          setSelectedItemId(nextTrip.document.days[safeDayIndex]?.items[0]?.item_id ?? '')
        }
      }
      editIntentRef.current = null
      setEditPanel(null)
      return true
    } catch (cause) {
      const status = classifyTravelWriteError(cause)
      transitionTripWrite({ ...identity, status })
      setWriteFeedback({ status, message: travelWriteErrorMessage(cause, status) })
      return false
    } finally {
      editingRef.current = false
      setEditing(false)
    }
  }

  async function retryEdit(): Promise<boolean> {
    const intent = editIntentRef.current
    if (!intent || writeFeedback?.status !== 'UNKNOWN') return false
    return sendEdit(intent)
  }

  async function refreshLatestTrip(): Promise<boolean> {
    if (!trip || refreshingLatest) return false
    setRefreshingLatest(true)
    try {
      const latest = await fetchTravelTripV2(trip.trip_id)
      if (latest.trip_id !== trip.trip_id) throw new Error('服务器返回了不匹配的行程。')
      setTrip((current) => current?.trip_id === latest.trip_id && current.revision < latest.revision ? latest : current)
      setUndoRevision(null)
      editIntentRef.current = null
      restoreIntentRef.current = null
      setEditPanel(null)
      setHistoryOpen(false)
      setWriteFeedback(null)
      setRestoreFeedback(null)
      setRefreshNotice(`已载入服务器最新 Revision v${latest.revision}；原修改未自动重放，请重新编辑。`)
      return true
    } catch (cause) {
      setWriteFeedback({ status: 'CONFLICT', message: `刷新最新行程失败：${cause instanceof Error ? cause.message : '请检查网络后重试。'}` })
      setRestoreFeedback({ status: 'CONFLICT', message: '无法刷新最新行程；当前页面版本保持不变。' })
      return false
    } finally {
      setRefreshingLatest(false)
    }
  }

  async function restoreRevision(targetRevision: number): Promise<boolean> {
    if (!trip || undoing) return false
    const signature = JSON.stringify({ tripId: trip.trip_id, expectedRevision: trip.revision, targetRevision })
    const existing = restoreIntentRef.current
    const intent: RestoreIntent = {
      tripId: trip.trip_id,
      expectedRevision: trip.revision,
      targetRevision,
      key: existing?.signature === signature ? existing.key : createTravelV2IdempotencyKey(),
      signature,
      baseTrip: trip,
    }
    return sendRestore(intent)
  }

  async function sendRestore(intent: RestoreIntent): Promise<boolean> {
    if (undoing) return false
    const identity = { kind: 'restore' as const, tripId: intent.tripId, signature: intent.signature, key: intent.key }
    if (!transitionTripWrite({ ...identity, status: 'VALIDATING' })) return false
    restoreIntentRef.current = intent
    setUndoing(true)
    setRestoreFeedback({ status: 'VALIDATING', message: `正在确认恢复目标 v${intent.targetRevision}…` })
    await nextPaint()
    transitionTripWrite({ ...identity, status: 'SAVING' })
    setRestoreFeedback({ status: 'SAVING', message: '正在校验并提交恢复操作…' })
    try {
      const result = await restoreTravelTripV2(intent.tripId, {
        expected_revision: intent.expectedRevision,
        target_revision: intent.targetRevision,
      }, intent.key)
      if (!isConfirmedTravelWrite(result, intent.tripId, intent.expectedRevision)) {
        transitionTripWrite({ ...identity, status: 'UNKNOWN' })
        const message = '恢复回执尚不能证明事务提交成功；当前行程保持上次确认版本。可用同一请求安全重试。'
        setRestoreFeedback({ status: 'UNKNOWN', message })
        setWriteFeedback({ status: 'UNKNOWN', message })
        setHistoryOpen(true)
        return false
      }
      const nextTrip = { ...intent.baseTrip, revision: result.revision, document: result.document }
      const current = tripRef.current
      if (current?.trip_id === intent.tripId && current.revision === intent.expectedRevision) {
        setTrip(nextTrip)
        setUndoRevision(intent.expectedRevision)
      }
      restoreIntentRef.current = null
      transitionTripWrite({ ...identity, status: 'SAVED' })
      const message = current && current.revision > result.revision
        ? `恢复操作已由服务端确认新增 v${result.revision}；页面已载入更新的 v${current.revision}，保留当前版本。`
        : `已恢复到 v${intent.targetRevision} 的内容，并新增 v${result.revision}。`
      setRestoreFeedback({ status: 'SAVED', message })
      setWriteFeedback({ status: 'SAVED', message })
      setRestoreRequest(null)
      setSavingMessage('')
      setError('')
      return true
    } catch (cause) {
      const status = classifyTravelWriteError(cause)
      const message = travelWriteErrorMessage(cause, status)
      transitionTripWrite({ ...identity, status })
      if (status !== 'UNKNOWN') restoreIntentRef.current = null
      setRestoreFeedback({ status, message })
      setWriteFeedback({ status, message })
      if (status === 'UNKNOWN') setHistoryOpen(true)
      return false
    } finally {
      setUndoing(false)
    }
  }

  async function retryRestore(): Promise<boolean> {
    const intent = restoreIntentRef.current
    if (!intent || restoreFeedback?.status !== 'UNKNOWN') return false
    return sendRestore(intent)
  }

  async function undo() {
    if (undoRevision !== null) await restoreRevision(undoRevision)
  }

  function selectSearch(tab: Exclude<Tab, 'trip'>) {
    if (pendingTripWriteRef.current) return
    setActiveTab(tab)
    setSelectedLocation(null)
    setSavingMessage('')
  }

  /** 时区横幅关闭记忆（ux2 #1）：同行程只提示一次，不再常驻占屏。 */
  useEffect(() => {
    let dismissed = false
    try {
      dismissed = window.localStorage.getItem(`travel-tz-banner-dismissed:${tripId}`) === '1'
    } catch { /* 隐私模式等存取失败按未关闭处理 */ }
    setTzBannerDismissed(dismissed)
  }, [tripId])

  function dismissTzBanner() {
    setTzBannerDismissed(true)
    try {
      window.localStorage.setItem(`travel-tz-banner-dismissed:${tripId}`, '1')
    } catch { /* 忽略：会话内仍保持关闭 */ }
  }

  /** 餐食占位 → 就餐时段推断：标题优先，其次开始时间（<15 点算午餐）。 */
  function inferMealPeriod(item: TripItemV2): 'lunch' | 'dinner' {
    if (item.title.includes('晚') || item.title.includes('夜')) return 'dinner'
    if (item.title.includes('午')) return 'lunch'
    const hour = Number.parseInt((item.start_time ?? '').slice(0, 2), 10)
    return Number.isFinite(hour) && hour >= 15 ? 'dinner' : 'lunch'
  }

  /** 编辑弹窗「换家餐厅」（ux2 #2）：加入真实餐厅（selections/meals）+ 移除占位
   * 餐项（remove_item）顺序双写；每步独立幂等键与写状态机守卫，UNKNOWN 不冒充
   * 成功也不自动重试（避免同一请求重复提交两份）。 */
  async function switchMeal(merchant: MerchantSearchResultV2, placeholder: TripItemV2): Promise<boolean> {
    const currentTrip = tripRef.current
    if (!currentTrip || Boolean(pendingTripWrite)) return false
    const day = currentTrip.document.days.find((entry) => entry.items.some((stop) => stop.item_id === placeholder.item_id))
    if (!day) return false
    const period = inferMealPeriod(placeholder)
    const periodLabel = period === 'lunch' ? '午餐' : '晚餐'

    // 第一步：加入餐厅（既有 selections/meals 单条端点）
    const addInput = {
      expected_revision: currentTrip.revision,
      day_id: day.day_id,
      meal_period: period,
      selection_id: merchant.selection_id,
      merchant: {
        merchant_id: merchant.merchant_id, name: merchant.name, address: merchant.address,
        lat: merchant.lat, lng: merchant.lng, rating: merchant.rating,
        open_status: merchant.open_status, open_time_today: merchant.open_time_today,
        avg_cost_cny: merchant.avg_cost_cny, source: merchant.source, observed_at: merchant.queried_at,
      } as MerchantSnapshotV2,
    }
    const addIdentity = {
      kind: 'selection' as const, tripId: currentTrip.trip_id,
      signature: JSON.stringify({ tripId: currentTrip.trip_id, add: addInput }),
      key: createTravelV2IdempotencyKey(),
    }
    // 当前未决写的身份（异常收敛用）：两步分别收敛，终态异常才释放守卫
    let activeIdentity: Pick<TravelTripWriteTransition, 'kind' | 'tripId' | 'signature' | 'key'> = addIdentity
    if (!transitionTripWrite({ ...addIdentity, status: 'VALIDATING' })) return false
    try {
      await nextPaint()
      transitionTripWrite({ ...addIdentity, status: 'SAVING' })
      const addResult = await addTravelMealV2(currentTrip.trip_id, addInput, addIdentity.key)
      if (!isConfirmedTravelWrite(addResult, currentTrip.trip_id, currentTrip.revision)) {
        transitionTripWrite({ ...addIdentity, status: 'UNKNOWN' })
        setError('「' + merchant.name + '」的加入回执待确认；为保留原请求已锁定其他写入，请刷新页面后在占位餐项「编辑」菜单重试。')
        return false
      }
      let latest: TravelTripV2 = { ...currentTrip, revision: addResult.revision, document: addResult.document }
      transitionTripWrite({ ...addIdentity, status: 'SAVED' })

      // 第二步：移除占位餐项（餐厅已确认落库，删除失败不回滚加入）
      const placeholderGone = !latest.document.days.some((entry) => entry.items.some((stop) => stop.item_id === placeholder.item_id))
      let removedPlaceholder = false
      if (!placeholderGone) {
        const removeOp: TripEditOperationV2 = { op: 'remove_item', item_id: placeholder.item_id }
        const removeIdentity = {
          kind: 'edit' as const, tripId: latest.trip_id,
          signature: JSON.stringify({ tripId: latest.trip_id, revision: latest.revision, remove: removeOp }),
          key: createTravelV2IdempotencyKey(),
        }
        activeIdentity = removeIdentity
        if (!transitionTripWrite({ ...removeIdentity, status: 'VALIDATING' })) return false
        await nextPaint()
        transitionTripWrite({ ...removeIdentity, status: 'SAVING' })
        const removeResult = await applyTravelTripEditV2(latest.trip_id, {
          expected_revision: latest.revision,
          change_summary: `已将「${placeholder.title}」换为「${merchant.name}」（移除占位餐项）`,
          operation: removeOp,
        }, removeIdentity.key)
        if (!isConfirmedTravelWrite(removeResult, latest.trip_id, latest.revision)) {
          transitionTripWrite({ ...removeIdentity, status: 'UNKNOWN' })
          setError(`「${merchant.name}」已加入${periodLabel}（v${addResult.revision}）；占位餐项删除回执待确认，请刷新页面后通过占位卡片「编辑」菜单重试删除。`)
          return false
        }
        latest = { ...latest, revision: removeResult.revision, document: removeResult.document }
        transitionTripWrite({ ...removeIdentity, status: 'SAVED' })
        removedPlaceholder = true
      }

      const current = tripRef.current
      if (current?.trip_id === latest.trip_id) {
        setTrip(latest)
        setUndoRevision(currentTrip.revision)
        const mealDayIndex = latest.document.days.findIndex((entry) => entry.items.some((stop) => stop.item_id === merchant.selection_id || stop.title === merchant.name))
        if (mealDayIndex >= 0) {
          setDayIndex(mealDayIndex)
          const newMeal = latest.document.days[mealDayIndex].items.find((stop) => stop.title === merchant.name)
          setSelectedItemId(newMeal?.item_id ?? '')
        }
      }
      setWriteFeedback({
        status: 'SAVED',
        message: removedPlaceholder
          ? `已将「${merchant.name}」加入${periodLabel}，并移除占位「${placeholder.title}」（v${latest.revision}）。`
          : `已将「${merchant.name}」加入${periodLabel}（v${latest.revision}）。`,
      })
      return true
    } catch (cause) {
      const status = classifyTravelWriteError(cause)
      // 明确失败族（INVALID/CONFLICT/FAILED）收敛释放守卫；UNKNOWN 语义 =
      // 服务端结果未知，保持守卫锁定并提示刷新后在原入口处理（见上方分支）。
      transitionTripWrite({ ...activeIdentity, status })
      setError(travelWriteErrorMessage(cause, status))
      return false
    }
  }

  /** 时间轴卡片点击：选中联动地图；候选切换统一走三点编辑弹窗（ux2 #2）。 */
  function selectTimelineItem(item: TripItemV2) {
    setSelectedItemId(item.item_id)
    setSelectedLocation(null)
  }

  const onSelectMapItem = useCallback((id: string) => {
    if (allMapItemIds.has(id)) {
      setSelectedItemId(id)
      setSelectedLocation(null)
    }
  }, [allMapItemIds])

  const editItem = editPanel?.kind === 'item'
    ? days.flatMap((entry) => entry.items).find((entry) => entry.item_id === editPanel.itemId) ?? null
    : null
  const editRoute = editPanel?.kind === 'transport'
    ? { fromItemId: editPanel.fromItemId, toItemId: editPanel.toItemId }
    : undefined

  if (loading) return <main className="flex min-h-screen items-center justify-center bg-[#f4f8fd] text-sm text-[#70839b]">正在读取 V2 行程…</main>
  if (!trip) return <main className="min-h-screen bg-[#f4f8fd] p-5 text-[#17283f]"><a href="/travel/itineraries" className="text-sm text-[#2878f5]">返回我的行程</a><p role="alert" className="mx-auto mt-8 max-w-lg rounded-xl bg-white p-5 text-sm text-rose-700">{error || '没有找到这条 V2 行程。'}</p></main>

  const brief = trip.document.brief
  const budget = brief.budget.amount === null ? '待设置' : `¥${brief.budget.amount.toLocaleString()}`
  const pendingWriteLabel = pendingTripWrite?.kind === 'edit'
    ? '编辑'
    : pendingTripWrite?.kind === 'restore' ? '恢复' : '选入'
  const pendingWriteMessage = pendingTripWrite?.status === 'UNKNOWN'
    ? `本行程的${pendingWriteLabel}请求结果待确认。为保留原请求，当前查询 Tab 和其他正式写入已锁定；请在原操作面板中使用同一请求安全重试。`
    : pendingTripWrite ? `本行程正在${pendingWriteLabel}，其他正式写入和 Tab 切换暂时锁定。` : ''
  const timezoneWarning = trip.document.health.issues.find((issue) =>
    issue.code === 'timezone_unconfirmed'
    || issue.code === 'timezone_assumed'
    || (issue.message.includes('时区') && /(未提供|暂用|暂按|默认|未确认|待确认|不代表)/.test(issue.message)),
  )

  return <main className="flex min-h-screen flex-none flex-col bg-[#f4f8fd] text-[#17283f] xl:h-screen xl:min-h-0">
    {conversationNotice && <p role="status" className="mx-3 mt-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">{conversationNotice}</p>}
    <header className="hidden h-[64px] shrink-0 items-center justify-between gap-4 border border-[#e6edf5] bg-white px-5 xl:mx-3 xl:mt-3 xl:flex xl:h-[64px] xl:rounded-2xl">
      <div className="flex min-w-0 items-center gap-3"><a href="/travel/itineraries" aria-label="返回行程列表" className="flex h-9 w-9 items-center justify-center rounded-full text-[#6b7f98] hover:bg-[#f3f7fc]"><ArrowLeft size={17} aria-hidden /></a><span className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#eaf2ff] text-[#2878f5]"><MapPin size={17} aria-hidden /></span><div className="min-w-0"><h1 className="truncate text-sm font-bold text-[#233a56]">{trip.title || brief.destination} · {brief.day_count} 天行程</h1><p className="mt-1 truncate text-[9px] text-[#8493a6]">{brief.start_date || '日期待定'} · {brief.travelers.adults + brief.travelers.children} 人 · 预算 {budget}</p></div></div>
      <div className="flex shrink-0 items-center gap-2"><span className="rounded-full bg-[#f1f6fc] px-3 py-1.5 text-[9px] font-medium text-[#6380a0]">V2 正式行程 · v{trip.revision}</span>{undoRevision !== null && <button type="button" disabled={Boolean(pendingTripWrite) || undoing} onClick={() => void undo()} className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-[#dce6f1] bg-white px-3 text-[10px] font-medium text-[#5b718d] disabled:opacity-50"><RotateCcw size={13} aria-hidden />{undoing ? '恢复中…' : '撤销'}</button>}<button type="button" onClick={() => setHistoryOpen(true)} aria-label="查看版本历史" className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-[#dce6f1] bg-white px-3 text-[10px] font-medium text-[#5b718d]"><History size={14} aria-hidden />历史</button><button type="button" aria-pressed={autoOptimizeOpen} disabled={Boolean(pendingTripWrite)} onClick={() => setAutoOptimizeOpen((value) => !value)} className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-[#b9d3fa] bg-[#f2f7ff] px-3 text-[10px] font-medium text-[#2878f5] disabled:opacity-50"><Sparkles size={14} aria-hidden />自动优选</button><button type="button" disabled={Boolean(pendingTripWrite)} onClick={() => setEditPanel(selectedItem ? { kind: 'item', itemId: selectedItem.item_id } : { kind: 'add' })} className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-[#2878f5] px-3.5 text-[10px] font-semibold text-white"><MoreHorizontal size={14} aria-hidden />{editMode ? '编辑模式' : '编辑行程'}</button></div>
    </header>

    <header className="sticky top-0 z-20 flex h-[56px] shrink-0 items-center justify-between border-b border-[#e5ecf4] bg-white px-3 xl:hidden"><a href="/travel/itineraries" aria-label="返回行程列表" className="flex h-9 w-9 items-center justify-center rounded-full text-[#536984]"><ArrowLeft size={18} aria-hidden /></a><div className="min-w-0 px-2 text-center"><h1 className="truncate text-sm font-semibold text-[#263b55]">{trip.title || brief.destination}</h1><p className="mt-0.5 text-[9px] text-[#92a0b1]">{brief.day_count} 天 · {brief.start_date || '日期待定'} · v{trip.revision}</p></div><div className="flex shrink-0 items-center gap-1.5"><button type="button" aria-pressed={autoOptimizeOpen} disabled={Boolean(pendingTripWrite)} onClick={() => setAutoOptimizeOpen((value) => !value)} className="min-h-10 rounded-full bg-[#f2f7ff] px-3 text-[10px] font-medium text-[#2878f5] disabled:opacity-50">优选</button>{conversationReady ? <a href="#travel-trip-conversation" className="inline-flex min-h-10 items-center gap-1 rounded-full border border-[#dce6f1] px-2.5 text-[10px] font-medium text-[#526b86]"><MessageCircle size={13} aria-hidden />对话</a> : <span aria-disabled="true" className="inline-flex min-h-10 items-center gap-1 rounded-full border border-[#e6ebf1] px-2.5 text-[10px] text-[#9aa8b8]">对话不可用</span>}<button type="button" onClick={() => setHistoryOpen(true)} aria-label="查看版本历史" className="min-h-10 rounded-full border border-[#dce6f1] px-3 text-[10px] font-medium text-[#526b86]">历史</button><button type="button" disabled={Boolean(pendingTripWrite)} onClick={() => setEditPanel(selectedItem ? { kind: 'item', itemId: selectedItem.item_id } : { kind: 'add' })} className="min-h-10 rounded-full bg-[#2878f5] px-3 text-[10px] font-semibold text-white">编辑</button></div></header>

    {writeFeedback && !editPanel && !historyOpen && <section
      role={travelWriteStatusRole(writeFeedback.status)}
      className={`mx-3 mt-3 rounded-xl border px-4 py-3 text-sm leading-5 lg:mx-4 lg:mt-4 ${travelWriteStatusTone(writeFeedback.status)}`}
    >
      <p><span className="mr-1.5 font-semibold">{travelWriteStatusLabel(writeFeedback.status)}</span>{writeFeedback.message}</p>
      {writeFeedback.status === 'UNKNOWN' && (editIntentRef.current || restoreIntentRef.current) && <button
        type="button"
        onClick={() => void (restoreIntentRef.current ? retryRestore() : retryEdit())}
        className="mt-2 min-h-10 rounded-lg border border-amber-300 bg-white px-3 text-sm font-semibold text-amber-950"
      >使用同一请求安全重试</button>}
      {writeFeedback.status === 'CONFLICT' && <button
        type="button"
        disabled={refreshingLatest}
        onClick={() => void refreshLatestTrip()}
        className="mt-2 min-h-10 rounded-lg border border-amber-300 bg-white px-3 text-sm font-semibold text-amber-950 disabled:opacity-50"
      >{refreshingLatest ? '正在刷新…' : '刷新最新版本并重新编辑'}</button>}
    </section>}
    {refreshNotice && <section role="status" className="mx-3 mt-3 rounded-xl border border-[#cfe0f5] bg-[#eff6ff] px-4 py-3 text-sm leading-5 text-[#315f91] lg:mx-4 lg:mt-4">{refreshNotice}</section>}
    {pendingTripWrite && <section
      role={pendingTripWrite.status === 'UNKNOWN' ? 'alert' : 'status'}
      className={`mx-3 mt-3 rounded-xl border px-4 py-3 text-sm leading-5 lg:mx-4 lg:mt-4 ${travelWriteStatusTone(pendingTripWrite.status)}`}
    >
      <p><span className="mr-1.5 font-semibold">{travelWriteStatusLabel(pendingTripWrite.status)}</span>{pendingWriteMessage}</p>
    </section>}
    {timezoneWarning && !tzBannerDismissed && <section role="note" className="mx-3 mt-3 flex items-start justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-5 text-amber-950 lg:mx-4 lg:mt-4">
      <p className="min-w-0">目的地时区尚未确认。当前记录的 {brief.timezone} 来自未确认的默认值或格式占位，不代表目的地当地时区；行程当地时间可能不准确，请确认后再依赖时间安排。</p>
      <button type="button" aria-label="关闭时区提示，本次行程不再显示" onClick={dismissTzBanner} className="shrink-0 rounded-full p-1 text-amber-700 hover:bg-amber-100"><X size={15} aria-hidden /></button>
    </section>}
    {autoOptimizeOpen && <AutoOptimizePanel trip={trip} disabled={Boolean(pendingTripWrite)} onTripSaved={saved} onClose={() => setAutoOptimizeOpen(false)} />}

    {/* 窄屏与桌面保留同一助手实例，切换布局不丢草稿或中断流式请求。 */}
    {/* 列宽对齐生成页（ux3）：三列均弹性比例，助手列不再固定 320px */}
    <div className="contents min-h-0 flex-1 gap-3 p-3 min-[821px]:grid min-[821px]:grid-cols-2 xl:grid-cols-[minmax(300px,.9fr)_minmax(340px,1.15fr)_minmax(360px,1fr)]">
      <aside id="travel-trip-conversation" aria-labelledby="travel-workspace-assistant-title" tabIndex={-1} className="order-3 mx-3 mb-24 flex h-[min(75dvh,640px)] min-h-0 min-w-0 shrink-0 scroll-mt-16 flex-col min-[821px]:col-span-2 min-[821px]:mx-0 min-[821px]:mb-0 xl:col-span-1 xl:h-auto overflow-hidden rounded-[22px] border border-[#e4ebf4] bg-white shadow-[0_6px_24px_rgba(31,74,130,.05)]"><header className="shrink-0 border-b border-[#edf1f6] px-4 py-3"><div className="flex items-center gap-2"><span className="flex h-8 w-8 items-center justify-center rounded-xl bg-[#eaf2ff] text-[#2878f5]"><Sparkles size={15} aria-hidden /></span><div><h2 id="travel-workspace-assistant-title" className="text-sm font-bold text-[#263b55]">AI 旅行助手</h2><p className="mt-0.5 text-[9px] text-[#8a98aa]">讨论或修改当前正式行程</p></div></div></header><div className="flex min-h-0 flex-1 flex-col overflow-hidden p-3"><details className="mb-2 shrink-0 rounded-lg border border-[#e8eef5] px-2.5 py-2"><summary className="cursor-pointer text-[9px] font-semibold text-[#536b86]">查询过程 · Tool 状态</summary><div className="mt-2"><TravelSearchActivity events={searchEvents}/></div></details><div className="flex min-h-0 flex-1 flex-col">{conversationReady ? <TravelV2TripConversation key={conversationId} trip={trip} conversationId={conversationId} conversation={conversation} onTripSaved={refreshAfterAIEdit} onRefreshTrip={refreshAfterAIConflict} onRestoreRequest={(revision) => { setRestoreRequest({ revision }); setHistoryOpen(true) }} embedded /> : <div role="status" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs leading-5 text-amber-900">{conversationNotice || '原始对话关联未验证；没有读取 URL 会话或历史记录。行程仍可查看和编辑。'}</div>}</div></div></aside>

      <section className="order-2 hidden min-h-0 min-[821px]:flex min-w-0 flex-col overflow-hidden rounded-[22px] border border-[#e4ebf4] bg-white shadow-[0_6px_24px_rgba(31,74,130,.05)]"><nav aria-label="行程与查询" className="flex shrink-0 gap-1 overflow-x-auto border-b border-[#edf1f6] px-3 pt-2.5">{TABS.map((tab) => <button key={tab.id} type="button" aria-pressed={activeTab === tab.id} disabled={Boolean(pendingTripWrite)} onClick={() => selectWorkspaceTab(tab.id)} className={`shrink-0 border-b-2 px-3 py-2 text-[11px] font-medium ${activeTab === tab.id ? 'border-[#2878f5] text-[#2878f5]' : 'border-transparent text-[#71839b] hover:text-[#2878f5]'}`}>{tab.label}</button>)}</nav>{activeTab === 'trip' ? <div className="flex min-h-0 flex-1 flex-col"><header className="shrink-0 border-b border-[#edf1f6] px-4 pb-3 pt-3.5"><div className="flex items-center justify-between"><div><h2 className="text-sm font-bold text-[#263b55]">{brief.destination} 行程时间轴</h2><p className="mt-1 text-[9px] text-[#8a98aa]">按天安排 · 与地图地点双向联动 · 天数在左侧地图列切换</p></div><button type="button" disabled={Boolean(pendingTripWrite)} onClick={() => selectedItem ? setEditPanel({ kind: 'item', itemId: selectedItem.item_id }) : setEditPanel({ kind: 'add' })} className={`rounded-lg px-2.5 py-2 text-[9px] font-medium bg-[#f4f7fb] text-[#73849a]`}>调整顺序</button></div></header><div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">{savingMessage && <div className="mb-3 flex items-center justify-between gap-2 rounded-lg bg-emerald-50 px-3 py-2 text-[9px] text-emerald-800"><span className="flex items-center gap-1"><Check size={12} aria-hidden />{savingMessage}</span>{undoRevision !== null && <button type="button" disabled={Boolean(pendingTripWrite) || undoing} onClick={() => void undo()} className="inline-flex shrink-0 items-center gap-1 font-medium"><RotateCcw size={11} aria-hidden />撤销</button>}</div>}{error && <p role="alert" className="mb-3 rounded-lg bg-rose-50 px-3 py-2 text-[9px] text-rose-700">{error}</p>}<div className="mb-3 flex items-center justify-between"><div><p className="text-[11px] font-semibold text-[#344b67]">第 {dayIndex + 1} 天 · {day?.date || '日期待定'}</p><p className="mt-1 text-[9px] text-[#93a0b1]">{day?.items.length ?? 0} 个安排</p></div></div>{day?.items.length ? <div className="space-y-1.5">{day.items.map((item, index) => <div key={item.item_id}><button type="button" onClick={() => selectTimelineItem(item)} className={`group flex w-full items-center gap-2.5 rounded-[15px] border p-2.5 text-left ${selectedItem?.item_id === item.item_id ? 'border-[#78aaf4] shadow-[0_4px_14px_rgba(40,120,245,.1)]' : 'border-[#e8eef5]'}`}><span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold text-white ${selectedItem?.item_id === item.item_id ? 'bg-[#175fc5]' : 'bg-[#2878f5]'}`}>{index + 1}</span><span className="min-w-0 flex-1"><span className="flex items-center justify-between gap-2"><span className="truncate text-[11px] font-semibold text-[#2b405b]">{item.title}</span><span className="shrink-0 text-[9px] text-[#8190a3]">{item.start_time || '时间待定'}</span></span><span className="mt-1 flex items-center gap-2 text-[9px] text-[#8190a3]"><span className="inline-flex items-center gap-1"><Clock3 size={10} aria-hidden />停留 {item.duration_min} 分钟</span>{item.activity_type === 'meal' && <span className="rounded bg-[#fff5e9] px-1.5 py-0.5 text-[#b57835]">餐饮</span>}</span><span className="mt-1 block truncate text-[9px] text-[#98a5b4]">{item.place?.address || item.note || item.place?.facts.source || '地点详情'}</span>{placeIntroLine(item) && <span className="hidden group-hover:block text-[9px] leading-4 text-[#5c7074]">{placeIntroLine(item)}</span>}</span><MoreHorizontal size={15} className="shrink-0 text-[#8798ad]" aria-hidden /></button>{index < day.items.length - 1 && <div className="ml-10 flex h-8 items-center gap-2"><span className="h-px w-4 border-t border-dashed border-[#bdd2ec]"/><button type="button" disabled={Boolean(pendingTripWrite)} onClick={() => setEditPanel({ kind: 'transport', fromItemId: item.item_id, toItemId: day.items[index + 1].item_id })} className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[9px] text-[#72849b]">交通 · {legLabel(day, item, day.items[index + 1])} <ChevronDown size={10} aria-hidden /></button></div>}</div>)}</div> : <div className="rounded-xl border border-dashed border-[#dce6f2] bg-[#fbfdff] p-6 text-center text-xs text-[#8797aa]">这一天还没有安排。</div>}</div></div> : <div className="min-h-0 flex-1 overflow-y-auto p-3"><TravelSearchPanel key={`${activeTab}-${trip.trip_id}`} initialKind={activeTab as Exclude<TravelSearchKind, 'trip'>} trip={trip} showTabs={false} onTripSaved={saved} onRefreshConflict={() => void refreshLatestTrip()} writeLocked={Boolean(pendingTripWrite)} onTripWriteTransition={transitionTripWrite} onLocationSelect={setSelectedLocation} onSearchEvent={recordSearchEvent}/></div>}</section>

      <aside className="order-1 hidden min-h-0 min-[821px]:flex min-w-0 flex-col gap-3 rounded-[22px] border border-[#e4ebf4] bg-white p-3 shadow-[0_6px_24px_rgba(31,74,130,.05)]"><div className="flex items-center justify-between px-1"><div><h2 className="text-sm font-bold text-[#263b55]">行程地图</h2><p className="mt-1 text-[9px] text-[#8a98aa]">天数条切换日期 · 点击编号定位行程地点</p></div><span className="rounded-full bg-[#f1f6fc] px-2.5 py-1 text-[9px] text-[#7589a1]">第 {dayIndex + 1} 天</span></div><div className="flex shrink-0 gap-2 overflow-x-auto pb-1">{days.map((item, index) => <button key={item.day_id} type="button" aria-pressed={dayIndex === index} onClick={() => selectDay(index)} className={`min-w-[88px] rounded-xl border px-3 py-2 text-left ${dayIndex === index ? 'border-[#76aaf4] bg-[#f2f7ff] text-[#246bd2]' : 'border-[#e6edf5] bg-white text-[#6e8097]'}`}><span className="block truncate text-[10px] font-semibold">第 {index + 1} 天{item.title?.trim() ? ` · ${item.title.trim()}` : ''}</span><span className="mt-1 block text-[8px]">{item.date || '日期待定'}</span></button>)}</div><div className="min-h-0 flex-1"><TravelV2Map days={days} activeDayIndex={dayIndex} selectedItemId={selectedItem?.item_id ?? ''} selectedLocation={selectedLocation} onSelectItem={onSelectMapItem}/></div></aside>
    </div>

    <div className="flex-1 pb-3 min-[821px]:hidden"><section className="mx-3 mt-3 rounded-[18px] border border-[#e4ebf4] bg-white p-3 shadow-[0_4px_16px_rgba(31,74,130,.04)]"><div className="flex items-start justify-between gap-3"><div><p className="text-[9px] font-medium text-[#5a87bd]">正式行程 · 自动保存</p><h2 className="mt-1 text-base font-bold text-[#263b55]">{trip.title || brief.destination}</h2><p className="mt-1 text-[10px] text-[#8796a8]">{brief.start_date || '日期待定'} · {brief.day_count} 天 · {brief.travelers.adults + brief.travelers.children} 人</p></div><span className="rounded-full bg-[#f1f6fc] px-2 py-1 text-[8px] text-[#6380a0]">v{trip.revision}</span></div><div className="mt-3 grid grid-cols-3 divide-x divide-[#e8edf4] rounded-xl bg-[#f7faff] py-2.5"><div className="text-center"><p className="text-[8px] text-[#91a0b1]">人数</p><p className="mt-1 text-[10px] font-semibold text-[#4b6380]">{brief.travelers.adults + brief.travelers.children} 位</p></div><div className="text-center"><p className="text-[8px] text-[#91a0b1]">预算</p><p className="mt-1 text-[10px] font-semibold text-[#4b6380]">{budget}</p></div><div className="text-center"><p className="text-[8px] text-[#91a0b1]">节奏</p><p className="mt-1 text-[10px] font-semibold text-[#4b6380]">{paceLabel(brief.pace)}</p></div></div></section>
      <nav aria-label="行程和查询切换" className="mx-3 mt-3 grid grid-cols-4 rounded-xl border border-[#e5ecf5] bg-white p-1">{TABS.map((tab) => <button key={tab.id} type="button" aria-pressed={activeTab === tab.id} disabled={Boolean(pendingTripWrite)} onClick={() => selectWorkspaceTab(tab.id)} className={`h-9 rounded-lg text-[10px] ${activeTab === tab.id ? 'bg-[#edf5ff] font-semibold text-[#2878f5]' : 'text-[#7e8da1]'}`}>{tab.label}</button>)}</nav>
      {activeTab !== 'trip' && undoRevision !== null && <div className="mx-3 mt-2 flex items-center justify-between gap-3 rounded-xl border border-[#cfe0f5] bg-[#eff6ff] px-3 py-2.5 text-xs leading-5 text-[#315f91] lg:hidden"><span>最近一次正式变更可撤销。</span><button type="button" aria-label="撤销最近一次正式变更" disabled={Boolean(pendingTripWrite) || undoing} onClick={() => void undo()} className="inline-flex min-h-10 shrink-0 items-center gap-1 rounded-lg px-2 font-medium disabled:opacity-50"><RotateCcw size={13} aria-hidden/>撤销</button></div>}      {activeTab === 'trip' ? <><div className="mx-3 mt-3 flex gap-2 overflow-x-auto pb-1">{days.map((item, index) => <button key={item.day_id} type="button" aria-pressed={dayIndex === index} onClick={() => selectDay(index)} className={`min-w-[92px] rounded-xl border px-3 py-2 text-center ${dayIndex === index ? 'border-[#79aaf2] bg-[#edf5ff] text-[#246bd2]' : 'border-[#e6edf5] bg-white text-[#788ba3]'}`}><span className="block truncate text-[10px] font-semibold">第 {index + 1} 天{item.title?.trim() ? ` · ${item.title.trim()}` : ''}</span><span className="mt-1 block text-[8px]">{item.date || '日期待定'}</span></button>)}</div><div className="mx-3 mt-2 h-[230px]"><TravelV2Map days={days} activeDayIndex={dayIndex} selectedItemId={selectedItem?.item_id ?? ''} selectedLocation={selectedLocation} onSelectItem={onSelectMapItem}/></div><section className="mx-3 mt-3 rounded-[20px] border border-[#e4ebf4] bg-white p-3 shadow-[0_4px_16px_rgba(31,74,130,.04)]"><header className="mb-3 flex items-center justify-between"><div><h2 className="text-xs font-bold text-[#2d435e]">第 {dayIndex + 1} 天安排</h2><p className="mt-1 text-[9px] text-[#92a0b1]">{day?.items.length ?? 0} 个地点 · 点击卡片联动地图</p></div><button type="button" disabled={Boolean(pendingTripWrite)} onClick={() => selectedItem ? setEditPanel({ kind: 'item', itemId: selectedItem.item_id }) : setEditPanel({ kind: 'add' })} className={`rounded-lg px-2.5 py-2 text-[9px] bg-[#f3f7fc] text-[#71839b]`}>调整顺序</button></header>{(savingMessage || undoRevision !== null) && <div className="mb-3 flex items-center justify-between gap-2 rounded-lg border border-[#cfe0f5] bg-[#eff6ff] px-3 py-2.5 text-xs leading-5 text-[#315f91]"><span className="flex items-center gap-1.5">{savingMessage && <Check size={14} aria-hidden />}{savingMessage || '最近一次正式变更可撤销。'}</span>{undoRevision !== null && <button type="button" aria-label="撤销最近一次正式变更" disabled={Boolean(pendingTripWrite) || undoing} onClick={() => void undo()} className="inline-flex min-h-10 shrink-0 items-center gap-1 rounded-lg px-2 font-medium disabled:opacity-50"><RotateCcw size={13} aria-hidden/>撤销</button>}</div>}{error && <p role="alert" className="mb-3 rounded-lg bg-rose-50 px-3 py-2 text-[9px] text-rose-700">{error}</p>}{day?.items.length ? <div className="space-y-2">{day.items.map((item, index) => <article key={item.item_id} className={`rounded-[15px] border p-2.5 ${selectedItem?.item_id === item.item_id ? 'border-[#78aaf4] shadow-[0_4px_14px_rgba(40,120,245,.1)]' : 'border-[#e8eef5]'}`}><div className="flex items-center gap-2.5"><button type="button" onClick={() => { setSelectedItemId(item.item_id); setSelectedLocation(null) }} aria-label={`地图定位到第 ${index + 1} 站 ${item.title}`} className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[#2878f5] text-[10px] font-semibold text-white">{index + 1}</button><button type="button" onClick={() => selectTimelineItem(item)} className="group min-w-0 flex-1 text-left"><span className="flex items-center justify-between gap-2"><span className="truncate text-xs font-semibold text-[#2b405b]">{item.title}</span><span className="text-[9px] text-[#8190a3]">{item.start_time || '时间待定'}</span></span><span className="mt-1 block truncate text-[9px] text-[#98a5b4]">{item.place?.address || item.note || `停留 ${item.duration_min} 分钟`}</span>{placeIntroLine(item) && <span className="hidden group-hover:block text-[9px] leading-4 text-[#5c7074]">{placeIntroLine(item)}</span>}</button><button type="button" disabled={Boolean(pendingTripWrite)} onClick={() => setEditPanel({ kind: 'item', itemId: item.item_id })} aria-label={`编辑${item.title}`} className="rounded-lg p-1 text-[#8798ad]"><MoreHorizontal size={16} aria-hidden/></button></div>{index < day.items.length - 1 && <button type="button" disabled={Boolean(pendingTripWrite)} onClick={() => setEditPanel({ kind: 'transport', fromItemId: item.item_id, toItemId: day.items[index + 1].item_id })} className="ml-10 mt-2 flex items-center gap-2 text-[9px] text-[#72849b]">交通 · {legLabel(day, item, day.items[index + 1])} <ChevronDown size={10} aria-hidden/></button>}</article>)}</div> : <div className="rounded-xl border border-dashed border-[#dce6f2] p-6 text-center text-xs text-[#8797aa]">这一天还没有安排。</div>}<button type="button" disabled={Boolean(pendingTripWrite)} onClick={() => setEditPanel({ kind: 'add' })} className="mt-3 h-10 w-full rounded-xl border border-dashed border-[#a9c7ed] bg-[#f6faff] text-xs font-semibold text-[#2878f5]">＋ 添加地点或活动</button></section></> : <div className="mx-3 mt-3"><TravelSearchPanel key={`mobile-${activeTab}-${trip.trip_id}`} initialKind={activeTab as Exclude<TravelSearchKind, 'trip'>} trip={trip} showTabs={false} onTripSaved={saved} onRefreshConflict={() => void refreshLatestTrip()} writeLocked={Boolean(pendingTripWrite)} onTripWriteTransition={transitionTripWrite} onLocationSelect={setSelectedLocation} onSearchEvent={recordSearchEvent}/></div>}
    </div>
    {editPanel && <TravelV2EditDialog
      panel={editPanel}
      trip={trip}
      dayId={day?.day_id ?? days[0]?.day_id ?? ''}
      item={editItem}
      route={editRoute}
      saving={editing}
      error=""
      writeStatus={writeFeedback?.status ?? null}
      writeMessage={writeFeedback?.message ?? ''}
      onClose={() => setEditPanel(null)}
      onEdit={applyEdit}
      onSwitchMeal={switchMeal}
      onRetry={retryEdit}
      onRefreshConflict={refreshLatestTrip}
    />}
    {historyOpen && <TravelTripHistoryPanel
      key={`${trip.trip_id}-history`}
      tripId={trip.trip_id}
      currentRevision={trip.revision}
      restoreRequest={restoreRequest}
      restoreStatus={restoreFeedback?.status ?? null}
      restoreMessage={restoreFeedback?.message ?? ''}
      writeBlockedMessage={pendingTripWrite && pendingTripWrite.kind !== 'restore' ? pendingWriteMessage : ''}
      canRetryRestore={Boolean(restoreIntentRef.current)}
      onClose={closeHistory}
      onRestore={(targetRevision) => { void restoreRevision(targetRevision) }}
      onRetryRestore={() => { void retryRestore() }}
      onRefreshLatest={() => { void refreshLatestTrip() }}
    />}
    <TravelBottomNav active="trips" />
  </main>
}

function legLabel(day: TripDayV2, from: TripItemV2, to: TripItemV2): string {
  const leg = day.legs.find((item) => item.from_item_id === from.item_id && item.to_item_id === to.item_id)
  if (!leg) return '待选择'
  const labels = { walk: '步行', drive: '驾车', transit: '公共交通', taxi: '出租车' }
  if (leg.duration_min === null || leg.reliability === 'unavailable') return `${labels[leg.selected_mode]} · 耗时未知`
  return `${labels[leg.selected_mode]} · ${leg.duration_min} 分钟`
}

/** 行程卡 hover 介绍行（M2.5 降噪模式的 V2 移植）：只用服务端真实 facts，
 * 没有「介绍正文」数据源（amap 不提供描述），不编造文案；无可述事实返回 null。 */
function placeIntroLine(item: TripItemV2): string | null {
  const facts = item.place?.facts
  const parts: string[] = []
  if (typeof facts?.rating === 'number' && facts.rating > 0) parts.push(`评分 ${facts.rating}`)
  if (facts?.ticket_price_cny === 0) parts.push('门票免费')
  else if (typeof facts?.ticket_price_cny === 'number' && facts.ticket_price_cny > 0) parts.push(`门票 ¥${facts.ticket_price_cny}`)
  if (typeof facts?.avg_cost_cny === 'number' && facts.avg_cost_cny > 0) parts.push(`人均 ¥${facts.avg_cost_cny}`)
  const hours = facts?.opening_hours
  if (hours && typeof hours === 'object' && !Array.isArray(hours)) {
    const open = (hours as Record<string, unknown>).open_time
    const close = (hours as Record<string, unknown>).close_time
    if (typeof open === 'string' && typeof close === 'string' && open && close) parts.push(`${open}-${close}`)
  }
  if (typeof facts?.open_status === 'string' && facts.open_status.trim()) parts.push(facts.open_status.trim())
  if (facts?.verification === 'verified') parts.push('坐标已核验')
  if (item.note.trim()) parts.push(item.note.trim())
  return parts.length ? parts.join(' · ') : null
}

