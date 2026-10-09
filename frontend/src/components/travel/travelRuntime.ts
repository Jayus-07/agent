import type { Itinerary, ItineraryItem, TravelStreamEvent } from '@/api/travel'

export type FactStatus = 'verified' | 'estimated' | 'unverified' | 'unknown'

export interface FactInput {
  source?: string | null
  verification_status?: string | null
}

export function classifyFact(fact: FactInput): FactStatus {
  if (fact.verification_status === 'unverified') return 'unverified'
  const source = (fact.source ?? '').toLowerCase()
  if (!source) return 'unknown'
  if (source.startsWith('official:') || source.startsWith('tencent:') || source.startsWith('live:')) {
    return 'verified'
  }
  if (source.startsWith('seed:') || source.startsWith('estimate:') || source.startsWith('local:')) {
    return 'estimated'
  }
  return 'unknown'
}

export type CostAvailabilityStatus = 'verified' | 'unavailable'

export interface CostAvailability {
  tickets: CostAvailabilityStatus
  meals: CostAvailabilityStatus
  lodging: CostAvailabilityStatus
  transit: CostAvailabilityStatus
  total: CostAvailabilityStatus
}

/**
 * 费用只在有可核验来源时展示金额。后端目前返回的 cost 仍可能包含本地
 * 种子和路线估算值，因此不能把数值存在误读为真实报价。
 */
export function costAvailability(itinerary: Itinerary): CostAvailability {
  const items = (itinerary.days ?? []).flatMap((day) => day.items ?? [])
  const visitItems = items.filter((item) => item.kind === 'visit' && item.poi)
  const legs = (itinerary.days ?? []).flatMap((day) => day.legs ?? [])
  const tickets = visitItems.length > 0 && visitItems.every((item) => (
    classifyFact({
      source: item.poi?.source,
      verification_status: item.poi?.verification_status,
    }) === 'verified'
  ))
  const transit = legs.length > 0 && legs.every((leg) => (
    !leg.is_estimate && classifyFact({ source: leg.source }) === 'verified'
  ))
  const result = {
    tickets: tickets ? 'verified' : 'unavailable',
    meals: 'unavailable',
    lodging: 'unavailable',
    transit: transit ? 'verified' : 'unavailable',
  } as const
  return {
    ...result,
    total: Object.values(result).every((status) => status === 'verified')
      ? 'verified' : 'unavailable',
  }
}

export interface TravelChangeSummary {
  fromVersion: number
  toVersion: number
  briefFields: string[]
  added: string[]
  removed: string[]
  moved: Array<{ poiId: string; name: string; fromDay: number; toDay: number }>
  itemChanges: ItineraryChange[]
  budgetDelta: number
}

export type ItineraryChangeKind = 'added' | 'removed' | 'replaced' | 'moved' | 'time_changed'

export interface ItineraryChange {
  kind: ItineraryChangeKind
  previousItem?: ItineraryItem
  nextItem?: ItineraryItem
  previousDay?: number
  nextDay?: number
  certainty?: 'identity' | 'inferred'
  details: {
    timeChanged?: boolean
    previousTime?: string
    nextTime?: string
  }
}

interface ItineraryEntry {
  item: ItineraryItem
  dayIndex: number
  dayDate: string | null
}

function itineraryEntries(itinerary: Itinerary): ItineraryEntry[] {
  const entries: ItineraryEntry[] = []
  for (const day of itinerary.days ?? []) {
    for (const item of day.items ?? []) {
      entries.push({ item, dayIndex: day.day_index, dayDate: day.day_date })
    }
  }
  return entries
}

function normalizedText(value: string | null | undefined): string {
  return (value ?? '').normalize('NFKC').trim().replace(/\s+/g, ' ').toLowerCase()
}

function poiIdentity(entry: ItineraryEntry): string | null {
  // ID 保留大小写语义，避免把两个仅大小写不同的稳定 ID 合并。
  const poiId = (entry.item.poi?.poi_id ?? '').normalize('NFKC').trim()
  const kind = normalizedText(entry.item.kind)
  return poiId && kind ? `${poiId}\u0000${kind}` : null
}

function exactFallbackIdentity(entry: ItineraryEntry): string | null {
  if (entry.item.poi?.poi_id?.trim()) return null
  const name = normalizedText(entry.item.poi?.name || entry.item.title)
  const kind = normalizedText(entry.item.kind)
  if (!name || !kind) return null
  return [name, kind, entry.dayIndex, normalizedText(entry.item.start), normalizedText(entry.item.end)].join('\u0000')
}

function uniqueIndex(entries: ItineraryEntry[], keyOf: (entry: ItineraryEntry) => string | null): Map<string, ItineraryEntry> {
  const counts = new Map<string, number>()
  const values = new Map<string, ItineraryEntry>()
  for (const entry of entries) {
    const key = keyOf(entry)
    if (!key) continue
    counts.set(key, (counts.get(key) ?? 0) + 1)
    values.set(key, entry)
  }
  return new Map([...values].filter(([key]) => counts.get(key) === 1))
}

function validDate(value: string | null | undefined): number | null {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null
  const timestamp = Date.parse(`${value}T00:00:00Z`)
  if (!Number.isFinite(timestamp) || new Date(timestamp).toISOString().slice(0, 10) !== value) return null
  return timestamp
}

function dateOffset(entry: ItineraryEntry, itinerary: Itinerary): number | null {
  const timestamp = validDate(entry.dayDate)
  if (timestamp == null) return null
  const firstDayDate = (itinerary.days ?? [])
    .slice()
    .sort((left, right) => left.day_index - right.day_index)
    .map((day) => validDate(day.day_date))
    .find((value): value is number => value != null)
  const base = validDate(itinerary.brief?.start_date) ?? firstDayDate
  return base == null ? null : Math.round((timestamp - base) / 86_400_000)
}

function compareDay(
  previousEntry: ItineraryEntry,
  nextEntry: ItineraryEntry,
  previous: Itinerary,
  next: Itinerary,
): 'same' | 'different' | 'unknown' {
  const previousOffset = dateOffset(previousEntry, previous)
  const nextOffset = dateOffset(nextEntry, next)
  if (previousOffset != null && nextOffset != null) {
    return previousOffset === nextOffset ? 'same' : 'different'
  }
  // 缺少日历日期时，只有日期基准和天数结构都相同才用 day_index 判移动。
  // 若仅有一侧有日期，或出发日期/天数变化，则宁可不推断。
  const sameStart = normalizedText(previous.brief?.start_date) === normalizedText(next.brief?.start_date)
  const sameDayCount = (previous.days ?? []).length === (next.days ?? []).length
  if (previousEntry.dayDate || nextEntry.dayDate || !sameStart || !sameDayCount) {
    return previousEntry.dayIndex === nextEntry.dayIndex ? 'same' : 'unknown'
  }
  return previousEntry.dayIndex === nextEntry.dayIndex ? 'same' : 'different'
}

function validMinutes(value: string | null | undefined): number | null {
  if (!value || !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value)) return null
  const [hours, minutes] = value.split(':').map(Number)
  return hours * 60 + minutes
}

function replacementTimesOverlap(previous: ItineraryItem, next: ItineraryItem): boolean {
  const oldStart = validMinutes(previous.start)
  const oldEnd = validMinutes(previous.end)
  const newStart = validMinutes(next.start)
  const newEnd = validMinutes(next.end)
  if (oldStart == null || oldEnd == null || newStart == null || newEnd == null) return false
  const oldDuration = oldEnd - oldStart
  const newDuration = newEnd - newStart
  if (oldDuration <= 0 || newDuration <= 0) return false
  const overlap = Math.max(0, Math.min(oldEnd, newEnd) - Math.max(oldStart, newStart))
  const shorterDuration = Math.min(oldDuration, newDuration)
  const durationRatio = oldDuration / newDuration
  return overlap / shorterDuration >= 0.7
    && Math.abs(oldStart - newStart) <= 30
    && durationRatio >= 0.5 && durationRatio <= 2
}

function itemTime(item: ItineraryItem): string | undefined {
  const start = item.start?.trim()
  const end = item.end?.trim()
  if (!start && !end) return undefined
  return start && end ? `${start}–${end}` : `${start || '时间未定'}–${end || '时间未定'}`
}

function itemTimeChanged(previous: ItineraryItem, next: ItineraryItem): boolean {
  return normalizedText(previous.start) !== normalizedText(next.start)
    || normalizedText(previous.end) !== normalizedText(next.end)
}

function compareEntries(left: ItineraryEntry, right: ItineraryEntry): number {
  return left.dayIndex - right.dayIndex
    || normalizedText(left.item.title).localeCompare(normalizedText(right.item.title))
    || normalizedText(left.item.start).localeCompare(normalizedText(right.item.start))
    || normalizedText(left.item.poi?.poi_id).localeCompare(normalizedText(right.item.poi?.poi_id))
}

function budgetTotal(itinerary: Itinerary): number {
  const cost = itinerary.cost ?? { tickets: 0, meals: 0, lodging: 0, transit: 0 }
  return cost.total ?? (cost.tickets ?? 0) + (cost.meals ?? 0) + (cost.lodging ?? 0) + (cost.transit ?? 0)
}

function sameBriefValue(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true
  if (left == null || right == null) return false
  try {
    return JSON.stringify(left) === JSON.stringify(right)
  } catch {
    return false
  }
}

export function buildChangeSummary(previous: Itinerary, next: Itinerary): TravelChangeSummary {
  const oldBrief = (previous.brief ?? {}) as unknown as Record<string, unknown>
  const newBrief = (next.brief ?? {}) as unknown as Record<string, unknown>
  const briefFields = Object.keys({ ...oldBrief, ...newBrief })
    .filter((key) => key !== 'version' && !sameBriefValue(oldBrief[key], newBrief[key]))
    .sort()

  const oldEntries = itineraryEntries(previous)
  const newEntries = itineraryEntries(next)
  const matchedOld = new Set<ItineraryEntry>()
  const matchedNew = new Set<ItineraryEntry>()
  const itemChanges: ItineraryChange[] = []

  const matchUnique = (keyOf: (entry: ItineraryEntry) => string | null) => {
    const oldUnique = uniqueIndex(oldEntries.filter((entry) => !matchedOld.has(entry)), keyOf)
    const newUnique = uniqueIndex(newEntries.filter((entry) => !matchedNew.has(entry)), keyOf)
    for (const [key, oldEntry] of oldUnique) {
      const newEntry = newUnique.get(key)
      if (!newEntry) continue
      matchedOld.add(oldEntry)
      matchedNew.add(newEntry)
      const dayRelation = compareDay(oldEntry, newEntry, previous, next)
      const timeChanged = itemTimeChanged(oldEntry.item, newEntry.item)
      if (dayRelation === 'different') {
        itemChanges.push({
          kind: 'moved', previousItem: oldEntry.item, nextItem: newEntry.item,
          previousDay: oldEntry.dayIndex, nextDay: newEntry.dayIndex, certainty: 'identity',
          details: {
            timeChanged,
            ...(timeChanged ? { previousTime: itemTime(oldEntry.item), nextTime: itemTime(newEntry.item) } : {}),
          },
        })
      } else if (timeChanged) {
        itemChanges.push({
          kind: 'time_changed', previousItem: oldEntry.item, nextItem: newEntry.item,
          previousDay: oldEntry.dayIndex, nextDay: newEntry.dayIndex, certainty: 'identity',
          details: {
            timeChanged: true,
            previousTime: itemTime(oldEntry.item), nextTime: itemTime(newEntry.item),
          },
        })
      }
    }
  }

  // POI ID 是地点身份，不一定是行程实例；只有前后两版均唯一时才优先匹配。
  matchUnique(poiIdentity)
  // 无稳定 ID 时仅用唯一、同日、同类型、同时间的完全匹配消除未变化项目。
  matchUnique(exactFallbackIdentity)

  let removedEntries = oldEntries.filter((entry) => !matchedOld.has(entry)).sort(compareEntries)
  let addedEntries = newEntries.filter((entry) => !matchedNew.has(entry)).sort(compareEntries)
  const removedToConsume = new Set<ItineraryEntry>()
  const addedToConsume = new Set<ItineraryEntry>()
  const byDay = new Map<number, { removed: ItineraryEntry[]; added: ItineraryEntry[] }>()
  for (const entry of removedEntries) {
    if (entry.item.kind !== 'visit' || !entry.item.poi?.poi_id) continue
    const candidates = byDay.get(entry.dayIndex) ?? { removed: [], added: [] }
    candidates.removed.push(entry)
    byDay.set(entry.dayIndex, candidates)
  }
  for (const entry of addedEntries) {
    if (entry.item.kind !== 'visit' || !entry.item.poi?.poi_id) continue
    const candidates = byDay.get(entry.dayIndex) ?? { removed: [], added: [] }
    candidates.added.push(entry)
    byDay.set(entry.dayIndex, candidates)
  }
  for (const { removed: oldCandidates, added: newCandidates } of byDay.values()) {
    if (oldCandidates.length !== 1 || newCandidates.length !== 1) continue
    const oldEntry = oldCandidates[0]
    const newEntry = newCandidates[0]
    const oldIdentity = poiIdentity(oldEntry)
    const newIdentity = poiIdentity(newEntry)
    if (!oldIdentity || !newIdentity || oldIdentity === newIdentity) continue
    // 仅当候选身份在各自行程中唯一时，才允许推断替换。
    if (uniqueIndex(oldEntries, poiIdentity).get(oldIdentity) !== oldEntry
      || uniqueIndex(newEntries, poiIdentity).get(newIdentity) !== newEntry) continue
    if (!replacementTimesOverlap(oldEntry.item, newEntry.item)) continue
    removedToConsume.add(oldEntry)
    addedToConsume.add(newEntry)
    itemChanges.push({
      kind: 'replaced', previousItem: oldEntry.item, nextItem: newEntry.item,
      previousDay: oldEntry.dayIndex, nextDay: newEntry.dayIndex, certainty: 'inferred',
      details: {
        timeChanged: itemTimeChanged(oldEntry.item, newEntry.item),
        previousTime: itemTime(oldEntry.item), nextTime: itemTime(newEntry.item),
      },
    })
  }
  removedEntries = removedEntries.filter((entry) => !removedToConsume.has(entry))
  addedEntries = addedEntries.filter((entry) => !addedToConsume.has(entry))

  for (const entry of removedEntries) {
    itemChanges.push({
      kind: 'removed', previousItem: entry.item, previousDay: entry.dayIndex, details: {},
    })
  }
  for (const entry of addedEntries) {
    itemChanges.push({
      kind: 'added', nextItem: entry.item, nextDay: entry.dayIndex, details: {},
    })
  }
  itemChanges.sort((left, right) => {
    const kindOrder: Record<ItineraryChangeKind, number> = {
      replaced: 0, moved: 1, time_changed: 2, removed: 3, added: 4,
    }
    const leftTitle = normalizedText(left.previousItem?.title || left.nextItem?.title)
    const rightTitle = normalizedText(right.previousItem?.title || right.nextItem?.title)
    return (left.previousDay ?? left.nextDay ?? 0) - (right.previousDay ?? right.nextDay ?? 0)
      || kindOrder[left.kind] - kindOrder[right.kind]
      || leftTitle.localeCompare(rightTitle)
      || normalizedText(left.previousItem?.start || left.nextItem?.start)
        .localeCompare(normalizedText(right.previousItem?.start || right.nextItem?.start))
      || normalizedText(left.previousItem?.poi?.poi_id || left.nextItem?.poi?.poi_id)
        .localeCompare(normalizedText(right.previousItem?.poi?.poi_id || right.nextItem?.poi?.poi_id))
  })

  const moved = itemChanges
    .filter((change) => change.kind === 'moved' && change.nextItem?.poi?.poi_id)
    .map((change) => ({
      poiId: change.nextItem!.poi!.poi_id,
      name: change.nextItem!.poi!.name || change.nextItem!.title,
      fromDay: change.previousDay ?? 0,
      toDay: change.nextDay ?? 0,
    }))
    .sort((a, b) => a.poiId.localeCompare(b.poiId))
  const added = [...new Set(itemChanges
    .filter((change) => change.kind === 'added')
    .map((change) => change.nextItem?.poi?.poi_id)
    .filter((id): id is string => Boolean(id)))].sort()
  const removed = [...new Set(itemChanges
    .filter((change) => change.kind === 'removed')
    .map((change) => change.previousItem?.poi?.poi_id)
    .filter((id): id is string => Boolean(id)))].sort()

  return {
    fromVersion: previous.plan_version,
    toVersion: next.plan_version,
    briefFields,
    added,
    removed,
    moved,
    itemChanges,
    budgetDelta: budgetTotal(next) - budgetTotal(previous),
  }
}

export type TravelRunStatus = 'running' | 'completed' | 'stopped' | 'error'

export interface TravelRunState {
  runId: string
  lastSeq: number
  status: TravelRunStatus
}

export function acceptTravelRunEvent(
  state: TravelRunState,
  event: { runId: string; seq: number; status: TravelRunStatus },
): TravelRunState {
  if (event.runId !== state.runId || event.seq <= state.lastSeq) return state
  return { runId: state.runId, lastSeq: event.seq, status: event.status }
}

export type TravelProcessStatus = 'running' | 'completed' | 'error' | 'stopped'
export type TravelStageProcessStatus = 'running' | 'completed' | 'failed'

export interface TravelRequirementInterpretation {
  brief: Record<string, unknown>
  assumptions: string[]
  missing: string[]
  confidence?: string
  /** 会话意图（后端 classify_intent）：out_of_scope 时聊天流给出域引导卡 */
  intent?: string
}

export type TravelToolProcessStatus = 'running' | 'success' | 'degraded' | 'failed' | 'blocked'

export interface TravelProcessTool {
  tool: string
  toolCallId?: string
  taskId?: string
  /**
   * Tool Failure ≠ Workflow Failure（2026-10-07 容错契约）：
   * degraded = 实时数据未验证但行程继续生成；failed = 该次调用失败
   * （上层已重试/降级）；blocked = 硬依赖无法满足，整轮才会终止。
   * 三者都不再联动把整轮 run 标成 error。
   */
  status: TravelToolProcessStatus
  dataStatus?: string
  resultCount?: number
  durationMs?: number
  errorType?: string
  error?: string
  /** 用户可读的降级说明（后端 user_safe_message，无内部堆栈） */
  userMessage?: string
  category?: string
  preview?: Array<Record<string, unknown>>
}

export interface TravelProcessState {
  runId: string
  lastSeq: number
  status: TravelProcessStatus
  stages: Record<string, { status: TravelStageProcessStatus }>
  tools: TravelProcessTool[]
  requirement?: TravelRequirementInterpretation
  error?: string
}

export function travelProcessStatusLabel({
  loading,
  stopped,
  status,
}: {
  loading: boolean
  stopped: boolean
  status?: TravelProcessStatus
}): string {
  if (loading) return '正在处理'
  if (status === 'error') return '执行失败'
  if (stopped) return '已停止'
  if (status === 'running') return '进行中'
  if (status === 'completed') return '已返回结果'
  return '等待输入'
}

export function initialTravelProcess(runId: string): TravelProcessState {
  return { runId, lastSeq: 0, status: 'running', stages: {}, tools: [] }
}

function stringField(data: Record<string, unknown>, key: string): string | undefined {
  return typeof data[key] === 'string' && data[key] ? data[key] as string : undefined
}

function numberField(data: Record<string, unknown>, key: string): number | undefined {
  return typeof data[key] === 'number' ? data[key] as number : undefined
}

export function reduceTravelStreamEvent(
  state: TravelProcessState,
  event: TravelStreamEvent,
): TravelProcessState {
  const data = event.data
  const runId = stringField(data, 'run_id')
  const seq = numberField(data, 'seq')
  if (runId !== state.runId || seq == null || seq <= state.lastSeq) return state

  const next: TravelProcessState = {
    ...state,
    lastSeq: seq,
    stages: { ...state.stages },
    tools: state.tools.map((tool) => ({ ...tool })),
  }

  if (event.event === 'stage.started') {
    const stage = stringField(data, 'stage')
    if (stage) next.stages[stage] = { status: 'running' }
  } else if (event.event === 'requirement.interpreted') {
    const brief = data.brief
    next.requirement = {
      brief: brief && typeof brief === 'object' ? brief as Record<string, unknown> : {},
      assumptions: Array.isArray(data.assumptions)
        ? data.assumptions.filter((item): item is string => typeof item === 'string')
        : [],
      missing: Array.isArray(data.missing)
        ? data.missing.filter((item): item is string => typeof item === 'string')
        : [],
      confidence: stringField(data, 'confidence'),
      intent: stringField(data, 'intent'),
    }
  } else if (event.event === 'stage.finished') {
    const stage = stringField(data, 'stage')
    if (stage) {
      const failed = data.status === 'failed'
      next.stages[stage] = { status: failed ? 'failed' : 'completed' }
      if (failed) next.status = 'error'
    }
  } else if (event.event === 'tool.started') {
    const tool = stringField(data, 'tool')
    if (tool) {
      const started: TravelProcessTool = { tool, status: 'running' }
      const toolCallId = stringField(data, 'tool_call_id')
      const taskId = stringField(data, 'task_id')
      if (toolCallId) started.toolCallId = toolCallId
      if (taskId) started.taskId = taskId
      next.tools.push(started)
    }
  } else if (event.event === 'tool.result') {
    const tool = stringField(data, 'tool')
    if (tool) {
      const toolCallId = stringField(data, 'tool_call_id')
      const taskId = stringField(data, 'task_id')
      let actualIndex = -1
      if (toolCallId) {
        actualIndex = next.tools.findIndex((item) => item.toolCallId === toolCallId)
      } else {
        const reverseIndex = [...next.tools].reverse().findIndex((item) => (
          item.tool === tool && item.status === 'running'
        ))
        if (reverseIndex >= 0) actualIndex = next.tools.length - reverseIndex - 1
      }
      const current: TravelProcessTool = actualIndex >= 0
        ? next.tools[actualIndex]
        : { tool, status: 'running' }
      const updated: TravelProcessTool = {
        ...current,
        status: data.status === 'degraded'
          ? 'degraded'
          : data.status === 'blocked'
            ? 'blocked'
            : data.status === 'failed'
              ? 'failed'
              : 'success',
      }
      if (toolCallId) updated.toolCallId = toolCallId
      if (taskId) updated.taskId = taskId
      const userMessage = stringField(data, 'user_message')
      if (userMessage) updated.userMessage = userMessage
      const dataStatus = stringField(data, 'data_status')
      const errorType = stringField(data, 'error_type')
      const resultCount = numberField(data, 'result_count')
      const durationMs = numberField(data, 'duration_ms')
      const category = stringField(data, 'category')
      const error = stringField(data, 'error')
      if (dataStatus) updated.dataStatus = dataStatus
      if (errorType) updated.errorType = errorType
      if (resultCount != null) updated.resultCount = resultCount
      if (durationMs != null) updated.durationMs = durationMs
      if (category) updated.category = category
      if (error) updated.error = error
      if (Array.isArray(data.preview)) {
        updated.preview = data.preview.filter(
          (item): item is Record<string, unknown> => !!item && typeof item === 'object',
        )
      }
      if (actualIndex >= 0) next.tools[actualIndex] = updated
      else next.tools.push(updated)
      // Tool 失败/降级只标记该行，不再把整轮 run 拖成 error——
      // 只有 BLOCKED（硬依赖终止）与真正的 run 终止事件才改变轮次状态。
      if (updated.status === 'blocked') {
        next.status = 'error'
        next.error = userMessage || error || '硬依赖无法验证，已停止生成'
      }
    }
  } else if (event.event === 'error') {
    next.status = 'error'
    next.error = stringField(data, 'message') || '旅游规划执行失败'
  } else if (event.event === 'run.finished') {
    if (data.status === 'failed') next.status = 'error'
  } else if (event.event === 'done') {
    // needs_clarification 是旅游域正常返回的可行动结果，不是执行失败；
    // 只有服务端明确给出 failed 才把过程标成错误。
    next.status = data.status === 'failed' ? 'error' : 'completed'
    if (next.status === 'error') {
      next.error = stringField(data, 'message') || next.error || '旅游规划未完成'
    }
  }

  return next
}
