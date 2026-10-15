import type { TraceRecord } from '@/types/trace'
import type { SystemHealthSnapshot } from '@/api/observability'
import { safeDiagnosticCode, safeRuntimeLabel, getRunStatus, type RunStatus } from './runtimeStatus'

export type HealthSnapshotFreshness = 'fresh' | 'stale' | 'unknown'
export type HealthProbeState = 'available' | 'unavailable' | 'degraded' | 'unknown'

export interface SystemHealthRow {
  key: string
  label: string
  state: HealthProbeState
  value: string
}

export interface RuntimeAlertSample {
  traceId: string
  timestamp: string
  status: Extract<RunStatus, 'failed' | 'error' | 'degraded' | 'partial' | 'timeout'>
  code: string | null
}

export interface DegradationAlertInput {
  timestamp?: unknown
  level?: unknown
  code?: unknown
  message?: unknown
  detail?: unknown
}

export interface DegradationAlertSample {
  timestamp: string | null
  level: 'info' | 'warn' | 'error' | 'unknown'
  code: string | null
}

export interface SkillHealthInput {
  name?: unknown
  type?: unknown
  total?: unknown
  success?: unknown
  error?: unknown
  skipped?: unknown
  neutral?: unknown
  real_errors?: unknown
  retries?: unknown
  avg_duration_ms?: unknown
  success_rate?: unknown
  last_error_kind?: unknown
  last_error?: unknown
}

export interface SkillHealthRow {
  name: string
  type: string
  total: number
  success: number
  error: number
  skipped: number
  neutral: number
  realErrors: number
  retries: number
  averageDurationMs: number
  successRate: number | null
  lastErrorKind: 'denied' | 'not_configured' | 'failure' | null
}

const ALERT_STATUSES = new Set<RunStatus>(['failed', 'error', 'degraded', 'partial', 'timeout'])
const DEGRADATION_LEVELS = new Set(['info', 'warn', 'error'])
const SKILL_ERROR_KINDS = new Set(['denied', 'not_configured', 'failure'])
const SAFE_OPERATIONAL_CODE = /^[A-Za-z][A-Za-z0-9_.:/-]{0,63}$/

export function getHealthSnapshotFreshness(
  snapshot: SystemHealthSnapshot,
  nowMs = Date.now(),
  staleAfterMs = 90_000,
): HealthSnapshotFreshness {
  const collectedAt = snapshot.collected_at
  if (typeof collectedAt !== 'number' || !Number.isFinite(collectedAt)) return 'unknown'

  const ageMs = nowMs - collectedAt * 1000
  if (ageMs < -30_000) return 'unknown'
  return ageMs > staleAfterMs ? 'stale' : 'fresh'
}

export function getHealthProbeState(available: boolean | undefined): HealthProbeState {
  if (available === true) return 'available'
  if (available === false) return 'unavailable'
  return 'unknown'
}

function latencyLabel(value: number | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${value} ms` : '未采集'
}

/** 只使用系统健康 DTO 中的白名单字段，不回显错误正文或连接信息。 */
export function buildSystemHealthRows(snapshot: SystemHealthSnapshot): SystemHealthRow[] {
  const dbState = getHealthProbeState(snapshot.db?.available)
  const redisDbState = getHealthProbeState(snapshot.redis?.db0?.available)
  const brokerState = getHealthProbeState(snapshot.redis?.broker?.available)
  const resultState = getHealthProbeState(snapshot.redis?.result?.available)
  const workers = snapshot.worker?.online_count
  const workerState: HealthProbeState = snapshot.worker?.available === false
    ? 'unavailable'
    : snapshot.worker?.available === true
      ? typeof workers === 'number' && workers > 0 ? 'available' : 'degraded'
      : 'unknown'
  const queueEntries = Object.entries(snapshot.redis?.queues ?? {}).filter(([, value]) => Number.isFinite(value))
  const queueState: HealthProbeState = queueEntries.length > 0
    ? 'available'
    : brokerState === 'unavailable' ? 'unavailable' : 'unknown'

  return [
    {
      key: 'postgres', label: 'PostgreSQL', state: dbState,
      value: [
        `探测延迟 ${latencyLabel(snapshot.db?.latency_ms)}`,
        typeof snapshot.db?.active_connections === 'number' ? `活跃连接 ${snapshot.db.active_connections}` : null,
      ].filter(Boolean).join(' · '),
    },
    { key: 'redis-db0', label: 'Redis 缓存', state: redisDbState, value: `探测延迟 ${latencyLabel(snapshot.redis?.db0?.latency_ms)}` },
    { key: 'redis-broker', label: 'Redis 队列代理', state: brokerState, value: `探测延迟 ${latencyLabel(snapshot.redis?.broker?.latency_ms)}` },
    { key: 'redis-result', label: 'Redis 任务结果', state: resultState, value: `探测延迟 ${latencyLabel(snapshot.redis?.result?.latency_ms)}` },
    {
      key: 'worker', label: 'Celery Worker', state: workerState,
      value: typeof workers === 'number'
        ? `在线 ${workers} 个 · 活跃任务 ${snapshot.worker?.workers?.reduce((sum, worker) => sum + (worker.active_tasks ?? 0), 0) ?? 0}`
        : '在线数量未采集',
    },
    {
      key: 'queues', label: '任务队列长度', state: queueState,
      value: queueEntries.length > 0 ? queueEntries.map(([name, count]) => `${name}: ${count}`).join(' · ') : '队列读数未采集',
    },
    {
      key: 'tasks', label: '近 24 小时任务统计', state: getHealthProbeState(snapshot.tasks?.available),
      value: snapshot.tasks?.available === true ? '统计接口可读取；此状态不代表失败数为零' : '任务统计不可用或未采集',
    },
  ]
}

function safeOperationalIdentifier(value: unknown): string | null {
  const code = safeDiagnosticCode(value)
  return code && SAFE_OPERATIONAL_CODE.test(code) ? code : null
}

export function buildDegradationAlertSample(
  events: DegradationAlertInput[],
  limit = 300,
): DegradationAlertSample[] {
  return events.flatMap((event) => {
    const timestamp = typeof event.timestamp === 'string'
      && Number.isFinite(Date.parse(event.timestamp))
      ? event.timestamp
      : null
    const level = typeof event.level === 'string' && DEGRADATION_LEVELS.has(event.level)
      ? event.level as DegradationAlertSample['level']
      : 'unknown'
    const code = safeOperationalIdentifier(event.code)
    return [{ timestamp, level, code }]
  }).slice(0, limit)
}

function safeCount(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? Math.floor(value)
    : 0
}

function safeDuration(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? Math.round(value)
    : 0
}

export function buildSkillHealthRows(skills: SkillHealthInput[]): SkillHealthRow[] {
  return skills.flatMap((skill) => {
    const name = safeRuntimeLabel(skill.name, '')
    if (!name) return []

    const rawRate = skill.success_rate
    const successRate = typeof rawRate === 'number'
      && Number.isFinite(rawRate)
      && rawRate >= 0
      && rawRate <= 1
      ? rawRate
      : null
    const rawErrorKind = skill.last_error_kind
    const lastErrorKind = typeof rawErrorKind === 'string'
      && SKILL_ERROR_KINDS.has(rawErrorKind)
      ? rawErrorKind as SkillHealthRow['lastErrorKind']
      : null

    return [{
      name,
      type: safeRuntimeLabel(skill.type, '未知'),
      total: safeCount(skill.total),
      success: safeCount(skill.success),
      error: safeCount(skill.error),
      skipped: safeCount(skill.skipped),
      neutral: safeCount(skill.neutral),
      realErrors: safeCount(skill.real_errors),
      retries: safeCount(skill.retries),
      averageDurationMs: safeDuration(skill.avg_duration_ms),
      successRate,
      lastErrorKind,
    }]
  })
}

function safeOperationalCode(trace: TraceRecord): string | null {
  const error = trace.error ?? {}
  const raw = error.error_code ?? error.code ?? trace.metadata?.error_code
  return safeOperationalIdentifier(raw)
}

/**
 * 从经过服务端资源授权的 Trace 样本生成安全告警行。
 * 不返回错误正文、用户输入、Span 参数或任意 metadata。
 */
export function buildRuntimeAlertSample(traces: TraceRecord[], limit = 200): RuntimeAlertSample[] {
  return traces
    .flatMap((trace) => {
      const status = getRunStatus(trace)
      const timestamp = Date.parse(trace.timestamp || '')
      if (!ALERT_STATUSES.has(status) || !Number.isFinite(timestamp) || !trace.id) return []
      return [{
        traceId: trace.id,
        timestamp: trace.timestamp,
        status: status as RuntimeAlertSample['status'],
        code: safeOperationalCode(trace),
      }]
    })
    .sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp))
    .slice(0, limit)
}
