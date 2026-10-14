import type { TraceRecord } from '@/types/trace'
import type { SystemHealthSnapshot } from '@/api/observability'
import { getRunStatus, type RunStatus } from './runtimeStatus'

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

const ALERT_STATUSES = new Set<RunStatus>(['failed', 'error', 'degraded', 'partial', 'timeout'])
const SAFE_OPERATIONAL_CODE = /^[A-Z][A-Z0-9_.:/-]{0,63}$/

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

function safeOperationalCode(trace: TraceRecord): string | null {
  const error = trace.error ?? {}
  const raw = error.error_code ?? error.code ?? trace.metadata?.error_code
  if (typeof raw !== 'string') return null
  const code = raw.trim()
  return SAFE_OPERATIONAL_CODE.test(code) ? code : null
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
