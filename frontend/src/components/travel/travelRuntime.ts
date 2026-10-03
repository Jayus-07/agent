import type { Itinerary, TravelStreamEvent } from '@/api/travel'

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
  budgetDelta: number
}

interface PoiPlacement {
  day: number
  name: string
}

function poiPlacements(itinerary: Itinerary): Map<string, PoiPlacement> {
  const placements = new Map<string, PoiPlacement>()
  for (const day of itinerary.days ?? []) {
    for (const item of day.items ?? []) {
      if (item.poi?.poi_id) {
        placements.set(item.poi.poi_id, { day: day.day_index, name: item.poi.name || item.title })
      }
    }
  }
  return placements
}

function budgetTotal(itinerary: Itinerary): number {
  const cost = itinerary.cost
  return cost.total ?? cost.tickets + cost.meals + cost.lodging + cost.transit
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
  const oldBrief = previous.brief as unknown as Record<string, unknown>
  const newBrief = next.brief as unknown as Record<string, unknown>
  const briefFields = Object.keys({ ...oldBrief, ...newBrief })
    .filter((key) => key !== 'version' && !sameBriefValue(oldBrief[key], newBrief[key]))
    .sort()

  const oldPois = poiPlacements(previous)
  const newPois = poiPlacements(next)
  const added = [...newPois.keys()].filter((id) => !oldPois.has(id)).sort()
  const removed = [...oldPois.keys()].filter((id) => !newPois.has(id)).sort()
  const moved = [...newPois.entries()]
    .filter(([id, placement]) => oldPois.has(id) && oldPois.get(id)?.day !== placement.day)
    .map(([poiId, placement]) => ({
      poiId,
      name: placement.name,
      fromDay: oldPois.get(poiId)?.day ?? 0,
      toDay: placement.day,
    }))
    .sort((a, b) => a.poiId.localeCompare(b.poiId))

  return {
    fromVersion: previous.plan_version,
    toVersion: next.plan_version,
    briefFields,
    added,
    removed,
    moved,
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

export interface TravelProcessTool {
  tool: string
  status: 'running' | 'success' | 'failed'
  dataStatus?: string
  resultCount?: number
  durationMs?: number
  errorType?: string
  error?: string
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
    if (tool) next.tools.push({ tool, status: 'running' })
  } else if (event.event === 'tool.result') {
    const tool = stringField(data, 'tool')
    if (tool) {
      const index = [...next.tools].reverse().findIndex((item) => (
        item.tool === tool && item.status === 'running'
      ))
      const actualIndex = index < 0 ? -1 : next.tools.length - index - 1
      const current = actualIndex >= 0 ? next.tools[actualIndex] : { tool, status: 'running' as const }
      const updated: TravelProcessTool = {
        ...current,
        status: data.status === 'failed' ? 'failed' : 'success',
      }
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
      if (updated.status === 'failed') {
        next.status = 'error'
        next.error = error || errorType || 'Tool 执行失败'
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
