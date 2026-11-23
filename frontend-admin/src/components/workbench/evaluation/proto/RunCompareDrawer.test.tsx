import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { expect, it, vi } from 'vitest'
import { evaluationService, type EvalRunDetail } from '@/api/evaluation'
import type { PromptReleaseRecord } from '@/api/prompts'
import RunCompareDrawer, { buildCaseDiff, CASE_DIFF_KIND_LABELS } from './RunCompareDrawer'

vi.mock('@/lib/auth', () => ({ atLeast: () => true }))

function createTestQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } })
}

async function flushQueries() {
  for (let index = 0; index < 4; index += 1) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
  }
}

// ── buildCaseDiff 纯函数单测（批次 6c 核心归类口径）──────────────

function result(caseId: string, status: 'pass' | 'fail' | 'error' | 'skip', errorMsg = '') {
  return {
    case_id: caseId,
    module: 'sql',
    status,
    expected: {},
    actual: {},
    metrics: {},
    duration_ms: 10,
    error_msg: errorMsg || null,
  }
}

it('buildCaseDiff 按 pass→fail/fail→pass/同态/单侧归类并异常优先排序', () => {
  const baseline = [result('C-1', 'pass'), result('C-2', 'fail'), result('C-3', 'pass'), result('C-4', 'pass'), result('C-6', 'fail')]
  const candidate = [result('C-1', 'fail'), result('C-2', 'pass'), result('C-3', 'pass'), result('C-5', 'pass'), result('C-6', 'error')]
  const rows = buildCaseDiff(baseline, candidate)
  const kindOf = Object.fromEntries(rows.map(row => [row.caseId, row.kind]))
  expect(kindOf['C-1']).toBe('regressed')
  expect(kindOf['C-2']).toBe('improved')
  expect(kindOf['C-3']).toBe('both_pass')
  expect(kindOf['C-6']).toBe('both_fail')
  expect(kindOf['C-4']).toBe('baseline_only')
  expect(kindOf['C-5']).toBe('candidate_only')
  // 异常优先：regressed 在 improved 前；同态（both_pass）沉底
  expect(rows[0].kind).toBe('regressed')
  expect(rows[rows.length - 1].kind).toBe('both_pass')
})

it('buildCaseDiff error/skip 与 fail 同按未通过处理', () => {
  const rows = buildCaseDiff([result('C-1', 'pass')], [result('C-1', 'error')])
  expect(rows[0].kind).toBe('regressed')
  const rows2 = buildCaseDiff([result('C-1', 'skip')], [result('C-1', 'pass')])
  expect(rows2[0].kind).toBe('improved')
})

it('buildCaseDiff 空侧输入不抛错且归类为单侧', () => {
  const rows = buildCaseDiff([], [result('C-1', 'pass')])
  expect(rows).toHaveLength(1)
  expect(rows[0].kind).toBe('candidate_only')
})

// ── 抽屉渲染：汇总并排 + diff 表 + 默认折叠同态 ───────────────

function release(overrides: Partial<PromptReleaseRecord> & { release_id: string }): PromptReleaseRecord {
  return {
    prompt_key: 'sql.generator',
    version: 1,
    target_env: 'production',
    status: 'passed',
    eval_suite: 'sql_canonical',
    executor: 'local',
    dataset_provenance: {},
    tool_contract_fingerprint: '',
    model_binding_fingerprint: '',
    metrics: {},
    failure_reason: '',
    approved_by: '',
    published_by: '',
    created_at: '2026-11-22T12:00:00Z',
    ...overrides,
  } as PromptReleaseRecord
}

function runDetail(runId: string, statuses: Array<'pass' | 'fail' | 'error'>): EvalRunDetail {
  return {
    run_id: runId,
    report: {
      timestamp: '2026-11-22T12:00:00Z',
      module: 'sql',
      mode: 'offline',
      smoke: false,
      tier: '',
      summaries: [{ module: 'sql', total: statuses.length, passed: statuses.filter(s => s === 'pass').length, failed: statuses.filter(s => s === 'fail').length, errors: statuses.filter(s => s === 'error').length, skipped: 0, pass_rate: statuses.filter(s => s === 'pass').length / statuses.length, metrics: {} }],
      results: statuses.map((status, index) => result(`SC-${String(index + 1).padStart(3, '0')}`, status)),
      tier_summaries: [{ tier: 'smoke', total: statuses.length, passed: statuses.filter(s => s === 'pass').length, failed: 0, pass_rate: statuses.filter(s => s === 'pass').length / statuses.length, threshold: 0.95, passed_threshold: true }],
    },
  } as unknown as EvalRunDetail
}

async function mountDrawer(releases: PromptReleaseRecord[], focusReleaseId: string) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  const queryClient = createTestQueryClient()
  await act(async () => {
    root.render(<QueryClientProvider client={queryClient}><RunCompareDrawer releases={releases} focusReleaseId={focusReleaseId} onClose={() => {}} /></QueryClientProvider>)
  })
  await flushQueries()
  return { root, container, queryClient }
}

it('对比抽屉渲染两侧汇总与逐用例差异，同态默认折叠', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  const baselineRun = runDetail('run-base', ['pass', 'pass', 'fail'])
  const candidateRun = runDetail('run-cand', ['fail', 'pass', 'pass'])
  vi.spyOn(evaluationService, 'getRun').mockImplementation(async (runId: string) => runId === 'run-base' ? baselineRun : candidateRun)
  const releases = [
    release({ release_id: 'rel-base', eval_run_id: 'run-base', version: 3 }),
    release({ release_id: 'rel-cand', eval_run_id: 'run-cand', version: 4 }),
  ]
  const { root, container, queryClient } = await mountDrawer(releases, 'rel-cand')
  try {
    // 汇总：基准 66.7% vs 对比 66.7%
    expect(container.textContent).toContain('66.7%')
    // diff 计数徽标：1 退步（SC-001 pass→fail）+ 1 改善（SC-003 fail→pass）
    expect(container.textContent).toContain(CASE_DIFF_KIND_LABELS.regressed)
    expect(container.textContent).toContain(CASE_DIFF_KIND_LABELS.improved)
    // 同态默认折叠：SC-002 pass→pass（both_pass）不渲染，差异行渲染
    expect(container.textContent).toContain('SC-001')
    expect(container.textContent).toContain('SC-003')
    expect(container.textContent).not.toContain('SC-002')
    // 勾选"显示两侧通过"后出现
    const checkbox = container.querySelector('input[type=checkbox]') as HTMLInputElement
    await act(async () => { checkbox.click() })
    expect(container.textContent).toContain('SC-002')
  } finally {
    await act(async () => root.unmount())
    queryClient.clear()
    container.remove()
    vi.restoreAllMocks()
  }
})

it('默认基准侧自动选非预选的最新已发布记录', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  vi.spyOn(evaluationService, 'getRun').mockResolvedValue(runDetail('run-x', ['pass']))
  const releases = [
    release({ release_id: 'rel-old', eval_run_id: 'run-old', version: 2, status: 'published' }),
    release({ release_id: 'rel-new', eval_run_id: 'run-new', version: 4, status: 'passed' }),
  ]
  const { root, container, queryClient } = await mountDrawer(releases, 'rel-new')
  try {
    const selects = Array.from(container.querySelectorAll('select'))
    const baselineSelect = selects.find(sel => sel.getAttribute('aria-label') === '基准运行')
    expect(baselineSelect?.value).toBe('rel-old')
  } finally {
    await act(async () => root.unmount())
    queryClient.clear()
    container.remove()
    vi.restoreAllMocks()
  }
})
