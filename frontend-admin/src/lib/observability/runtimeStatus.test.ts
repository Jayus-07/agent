import { describe, expect, it } from 'vitest'
import type { TraceRecord } from '@/types/trace'
import { filterRuntimeRecords, getRunStatus, safeDiagnosticCode, safeRuntimeLabel, summarizeRuntimeSample } from './runtimeStatus'

function makeTrace(overrides: Partial<TraceRecord> = {}): TraceRecord {
  return {
    id: 'trace-1',
    request_id: 'request-1',
    timestamp: '2026-10-13T11:59:00.000Z',
    session_id: '',
    question: '',
    answer_preview: '',
    answer_len: 0,
    duration_ms: 100,
    model: { name: 'model', provider: 'provider' },
    usage: {},
    cost: {},
    error: {},
    metadata: {},
    spans: [],
    ...overrides,
  }
}

describe('Runtime run status', () => {
  it('keeps diagnostic labels structured and hides free-text values', () => {
    expect(safeRuntimeLabel('库存查询', '执行步骤')).toBe('库存查询')
    expect(safeRuntimeLabel('user@example.com', '执行步骤')).toBe('执行步骤')
    expect(safeDiagnosticCode('ProviderTimeout')).toBe('ProviderTimeout')
    expect(safeDiagnosticCode('failed for user@example.com')).toBeNull()
  })

  it('keeps terminal states distinct and does not present explicit business failure as success', () => {
    expect(getRunStatus(makeTrace({ status: 'success', metadata: { business_outcome: 'failed' } }))).toBe('failed')
    expect(getRunStatus(makeTrace({ status: 'success', error: { code: 'ProviderTimeout' } }))).toBe('error')
    expect(getRunStatus(makeTrace({ status: 'degraded' }))).toBe('degraded')
    expect(getRunStatus(makeTrace({ status: 'timeout' }))).toBe('timeout')
    expect(getRunStatus(makeTrace({ status: 'cancelled' }))).toBe('cancelled')
    expect(getRunStatus(makeTrace({ status: 'rejected' }))).toBe('rejected')
    expect(getRunStatus(makeTrace({ status: 'running' }))).toBe('running')
    expect(getRunStatus(makeTrace({ status: undefined }))).toBe('unknown')
  })

  it('calculates time-window sample metrics and only sums collected CNY costs', () => {
    const now = Date.parse('2026-10-13T12:00:00.000Z')
    const summary = summarizeRuntimeSample([
      makeTrace({ status: 'success', duration_ms: 100, cost_cny: 0.1 }),
      makeTrace({ id: 'failed', status: 'success', metadata: { business_outcome: 'failed' }, duration_ms: 200, cost_cny: 0.2 }),
      makeTrace({ id: 'error', status: 'success', error: { code: 'ProviderTimeout' }, duration_ms: 250 }),
      makeTrace({ id: 'degraded', status: 'degraded', duration_ms: 300 }),
      makeTrace({ id: 'running', status: 'running', duration_ms: 0, timestamp: '2026-10-13T11:58:00.000Z', cost_usd: 5 }),
      makeTrace({ id: 'old', timestamp: '2026-10-13T10:00:00.000Z', status: 'success', duration_ms: 1, cost_cny: 10 }),
    ], '1h', now)

    expect(summary.total).toBe(4)
    expect(summary.counts.success).toBe(1)
    expect(summary.counts.failed).toBe(1)
    expect(summary.counts.error).toBe(1)
    expect(summary.counts.degraded).toBe(1)
    expect(summary.counts.running).toBe(0)
    expect(summary.terminalCount).toBe(4)
    expect(summary.successRate).toBeCloseTo(1 / 4)
    expect(summary.p50DurationMs).toBe(200)
    expect(summary.p95DurationMs).toBe(300)
    expect(summary.costCny).toBeCloseTo(0.3)
    expect(summary.costRecordCount).toBe(2)
    expect(summary.total).toBe(filterRuntimeRecords([
      makeTrace({ status: 'success', duration_ms: 100, cost_cny: 0.1 }),
      makeTrace({ id: 'failed', status: 'success', metadata: { business_outcome: 'failed' }, duration_ms: 200, cost_cny: 0.2 }),
      makeTrace({ id: 'error', status: 'success', error: { code: 'ProviderTimeout' }, duration_ms: 250 }),
      makeTrace({ id: 'degraded', status: 'degraded', duration_ms: 300 }),
      makeTrace({ id: 'running', status: 'running', duration_ms: 0, timestamp: '2026-10-13T11:58:00.000Z', cost_usd: 5 }),
      makeTrace({ id: 'old', timestamp: '2026-10-13T10:00:00.000Z', status: 'success', duration_ms: 1, cost_cny: 10 }),
    ], '1h', now).length)
  })

  it('列表与统计共同排除未来时间戳和历史 running，并保留 unknown 记录', () => {
    const now = Date.parse('2026-10-13T12:00:00.000Z')
    const sample = [
      makeTrace({ id: 'success', status: 'success' }),
      makeTrace({ id: 'running', status: 'running' }),
      makeTrace({ id: 'unknown', status: undefined }),
      makeTrace({ id: 'future', status: 'success', timestamp: '2026-10-13T12:01:00.000Z' }),
    ]
    const listRecords = filterRuntimeRecords(sample, '1h', now)
    const summary = summarizeRuntimeSample(sample, '1h', now)

    expect(listRecords.map((trace) => trace.id)).toEqual(['success', 'unknown'])
    expect(summary.total).toBe(listRecords.length)
    expect(summary.counts.running).toBe(0)
  })
})
