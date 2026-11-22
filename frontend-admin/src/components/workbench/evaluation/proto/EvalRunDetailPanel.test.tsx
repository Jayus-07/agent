import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { expect, it, vi } from 'vitest'
import { evaluationService } from '@/api/evaluation'
import EvalRunDetailPanel from './EvalRunDetailPanel'

vi.mock('@/api/client', async importOriginal => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  errMessage: (cause: unknown) => (cause instanceof Error ? cause.message : String(cause)),
}))

function createTestQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } })
}

async function flushQueries() {
  for (let index = 0; index < 4; index += 1) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
  }
}

const DETAIL = {
  run_id: '2026-11-22T03-16-25-2836f7',
  report: {
    timestamp: '2026-11-22T03:16:40Z',
    module: 'sql',
    mode: 'live',
    smoke: false,
    tier: 'all',
    summaries: [{ module: 'sql', total: 15, passed: 6, failed: 9, errors: 0, skipped: 0, pass_rate: 0.4, metrics: {} }],
    results: [
      { case_id: 'SC-001', module: 'sql', status: 'fail', expected: { gold_sql: 'SELECT 1' }, actual: { sql: 'SELECT 2' }, metrics: { execution_match: 0 }, duration_ms: 1200, error_msg: '生成 SQL 结果集与 gold_sql 不一致', error_stage: null },
      { case_id: 'SC-002', module: 'sql', status: 'pass', expected: {}, actual: {}, metrics: { execution_match: 1 }, duration_ms: 800, error_msg: null, error_stage: null },
    ],
    tier_summaries: [
      { tier: 'smoke', total: 5, passed: 3, failed: 2, pass_rate: 0.6, threshold: 0.95, passed_threshold: false },
    ],
    metadata: { run_validity: { validity: 'VALID', invalid_reason: null } },
  },
  meta: {},
  run_status: null,
}

it('评测详情面板展示摘要、分层与逐题结果，并可展开单题明细', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  const getRun = vi.spyOn(evaluationService, 'getRun').mockResolvedValue(DETAIL as unknown as Awaited<ReturnType<typeof evaluationService.getRun>>)
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  const queryClient = createTestQueryClient()
  try {
    await act(async () => { root.render(<QueryClientProvider client={queryClient}><EvalRunDetailPanel runId="2026-11-22T03-16-25-2836f7" onClose={() => {}} /></QueryClientProvider>) })
    await flushQueries()
    expect(getRun).toHaveBeenCalledWith('2026-11-22T03-16-25-2836f7')
    expect(container.textContent).toContain('6/15 通过')
    expect(container.textContent).toContain('40.0%')
    expect(container.textContent).toContain('smoke 60%')
    expect(container.textContent).toContain('SC-001')
    expect(container.textContent).toContain('生成 SQL 结果集与 gold_sql 不一致')
    await act(async () => { (container.querySelector('tbody tr') as HTMLElement).click() })
    expect(container.textContent).toContain('gold_sql')
    expect(container.textContent).toContain('SELECT 2')
  } finally {
    await act(async () => root.unmount())
    queryClient.clear()
    container.remove()
    vi.restoreAllMocks()
  }
})

it('报告缺失时面板给出可重试的错误态', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  vi.spyOn(evaluationService, 'getRun').mockRejectedValue(new Error('Run not found: missing-run'))
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  const queryClient = createTestQueryClient()
  try {
    await act(async () => { root.render(<QueryClientProvider client={queryClient}><EvalRunDetailPanel runId="missing-run" onClose={() => {}} /></QueryClientProvider>) })
    await flushQueries()
    expect(container.textContent).toContain('Run not found')
    expect(Array.from(container.querySelectorAll('button')).some(button => button.textContent?.includes('重新读取'))).toBe(true)
  } finally {
    await act(async () => root.unmount())
    queryClient.clear()
    container.remove()
    vi.restoreAllMocks()
  }
})
