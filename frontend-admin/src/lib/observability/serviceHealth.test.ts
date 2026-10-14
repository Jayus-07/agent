import { describe, expect, it } from 'vitest'
import type { SystemHealthSnapshot } from '@/api/observability'
import type { TraceRecord } from '@/types/trace'
import { buildDegradationAlertSample, buildRuntimeAlertSample, buildSkillHealthRows, buildSystemHealthRows, getHealthProbeState, getHealthSnapshotFreshness } from './serviceHealth'

function trace(overrides: Partial<TraceRecord> = {}): TraceRecord {
  return {
    id: 'trace-1234567890',
    request_id: 'request-1',
    timestamp: '2026-10-14T08:00:00Z',
    session_id: 'session-1',
    question: 'private customer input',
    answer_preview: 'private answer',
    answer_len: 12,
    duration_ms: 10,
    model: { name: 'model', provider: 'provider' },
    usage: {},
    cost: {},
    error: {},
    metadata: {},
    spans: [],
    ...overrides,
  } as TraceRecord
}

describe('服务健康与告警样本', () => {
  it('接受仓库已有的小写和驼峰结构化诊断码', () => {
    const alerts = buildRuntimeAlertSample([
      trace({ status: 'degraded', error: { error_code: 'connect_error' } }),
      trace({ id: 'permission', status: 'failed', error: { error_code: 'permission_denied' } }),
      trace({ id: 'deadline', status: 'timeout', error: { error_code: 'workflow_deadline_exceeded' } }),
      trace({ id: 'provider', status: 'error', error: { error_code: 'ProviderTimeout' } }),
    ])

    expect(alerts.map((alert) => alert.code)).toEqual([
      'connect_error',
      'permission_denied',
      'workflow_deadline_exceeded',
      'ProviderTimeout',
    ])
  })

  it('保留结构化降级事件和 Skill 统计且不返回自由文本', () => {
    const degradation = buildDegradationAlertSample([
      {
        timestamp: '2026-10-14T08:00:00Z',
        level: 'warn',
        code: 'workflow_deadline_exceeded',
        message: 'private customer information',
      },
      {
        timestamp: '2026-10-14T08:01:00Z',
        level: 'error',
        code: 'user@example.com',
        detail: { private: 'must-not-render' },
      },
    ])
    const skills = buildSkillHealthRows([{
      name: 'rag.search',
      type: 'tool_call',
      total: 4,
      success: 3,
      error: 1,
      skipped: 0,
      neutral: 0,
      real_errors: 1,
      retries: 2,
      avg_duration_ms: 25,
      success_rate: 0.75,
      last_error_kind: 'failure',
      last_error: 'private customer information',
    }])

    expect(degradation).toEqual([
      { timestamp: '2026-10-14T08:00:00Z', level: 'warn', code: 'workflow_deadline_exceeded' },
      { timestamp: '2026-10-14T08:01:00Z', level: 'error', code: null },
    ])
    expect(skills[0]).toEqual({
      name: 'rag.search',
      type: 'tool_call',
      total: 4,
      success: 3,
      error: 1,
      skipped: 0,
      neutral: 0,
      realErrors: 1,
      retries: 2,
      averageDurationMs: 25,
      successRate: 0.75,
      lastErrorKind: 'failure',
    })
    expect(JSON.stringify([...degradation, ...skills])).not.toContain('private customer information')
    expect(JSON.stringify(degradation)).not.toContain('must-not-render')
  })

  it('只从获准 Trace 样本生成异常行，不返回用户内容或自由文本错误', () => {
    const alerts = buildRuntimeAlertSample([
      trace({
        status: 'success',
        metadata: { business_outcome: 'failed', private_field: 'do-not-render' },
        error: { error_code: 'DOMAIN_FAILED', message: 'private error message' },
      }),
      trace({ id: 'timeout-1', status: 'timeout', error: { error_code: '用户手机号13800138000' } }),
      trace({ id: 'success-1', status: 'success' }),
      trace({ id: 'running-1', status: 'running' }),
    ])

    expect(alerts).toEqual([
      { traceId: 'trace-1234567890', timestamp: '2026-10-14T08:00:00Z', status: 'failed', code: 'DOMAIN_FAILED' },
      { traceId: 'timeout-1', timestamp: '2026-10-14T08:00:00Z', status: 'timeout', code: null },
    ])
    expect(JSON.stringify(alerts)).not.toContain('private customer input')
    expect(JSON.stringify(alerts)).not.toContain('private error message')
    expect(JSON.stringify(alerts)).not.toContain('do-not-render')
    expect(JSON.stringify(alerts)).not.toContain('13800138000')
  })

  it('区分新鲜、过期和采集时间缺失的快照', () => {
    expect(getHealthSnapshotFreshness({ collected_at: 100 }, 150_000)).toBe('fresh')
    expect(getHealthSnapshotFreshness({ collected_at: 1 }, 120_000)).toBe('stale')
    expect(getHealthSnapshotFreshness({})).toBe('unknown')
    expect(getHealthSnapshotFreshness({ collected_at: 200 }, 100_000)).toBe('unknown')
  })

  it('真实队列长度 0 保留为 0，Worker 无在线实例显示异常，缺失探测字段显示未知', () => {
    const snapshot: SystemHealthSnapshot = {
      collected_at: 100,
      db: { available: true, latency_ms: 2, active_connections: 3 },
      redis: { db0: { available: true }, broker: { available: true }, queues: { celery: 0 } },
      worker: { available: true, online_count: 0, workers: [] },
      tasks: { available: true },
    }
    const rows = buildSystemHealthRows(snapshot)

    expect(rows.find((row) => row.key === 'postgres')?.state).toBe('available')
    expect(rows.find((row) => row.key === 'queues')).toMatchObject({ state: 'available', value: 'celery: 0' })
    expect(rows.find((row) => row.key === 'worker')?.state).toBe('degraded')
    expect(rows.find((row) => row.key === 'redis-result')?.state).toBe('unknown')
    expect(rows.find((row) => row.key === 'tasks')?.value).toContain('不代表失败数为零')
  })

  it('接口字段缺失和明确不可用采用不同状态', () => {
    expect(getHealthProbeState(undefined)).toBe('unknown')
    expect(getHealthProbeState(false)).toBe('unavailable')
    expect(getHealthProbeState(true)).toBe('available')
  })
})
