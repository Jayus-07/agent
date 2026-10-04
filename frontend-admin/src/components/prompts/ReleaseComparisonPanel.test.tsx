import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import ReleaseComparisonPanel from '@/components/prompts/ReleaseComparisonPanel'
import type { ReleaseComparison } from '@/api/promptComparison'

const getReleaseComparison = vi.fn()
vi.mock('@/api/promptComparison', () => ({
  getReleaseComparison: (...args: unknown[]) => getReleaseComparison(...args),
}))

beforeAll(() => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

const comparison: ReleaseComparison = {
  release_id: 'rel-1',
  prompt_key: 'rag.qa',
  candidate_version: 14,
  production_version: 12,
  candidate: {
    eval_run_id: 'run-1',
    metrics: { pass_rate: 0.95, 'recall@5': 0.9 },
    ragas: { ragas_faithfulness: 0.9 },
    gate: {
      tier_pass: true,
      sample_pass: true,
      regression_pass: false,
      ragas_pass: null,
      blocked_rules: [
        { rule: 'baseline_regression', expected: '降幅 ≤ 0.03', actual: '0.9→0.6', severity: 'error' },
      ],
    },
  },
  production_runs: [
    { run_id: 'run-0', pass_rate: 0.97, case_count: 26, pass_count: 25, metrics: { 'recall@5': 0.88 }, created_at: '2026-10-01' },
  ],
  baseline: { available: false, note: 'baseline unavailable（无基线，回归门已跳过并留痕）' },
  deltas: { available: false, note: 'baseline_unavailable: 无 baseline' },
}

let container: HTMLDivElement
let root: Root

describe('ReleaseComparisonPanel（C1-4/REG-08 审批对比）', () => {
  afterEach(() => {
    act(() => {
      root.unmount()
    })
    getReleaseComparison.mockReset()
  })

  it('渲染对比表 + 门禁 chips + blocked_rules 清单；无基线显式 unavailable', async () => {
    getReleaseComparison.mockResolvedValue(comparison)
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => {
      root.render(<ReleaseComparisonPanel promptKey="rag.qa" releaseId="rel-1" />)
    })
    await act(async () => { await Promise.resolve() })
    await act(async () => { await Promise.resolve() })
    const text = container.textContent || ''
    expect(text).toContain('Candidate v14 vs Production v12')
    expect(text).toContain('pass_rate')
    expect(text).toContain('0.9500')
    // GATE-16：结构化规则清单
    expect(text).toContain('baseline_regression')
    expect(text).toContain('回归门未过')
    // GATE-03：RAGAS 门未判定（audit 语义）
    expect(text).toContain('RAGAS 门: 未判定')
    // REG-10：无基线显式口径
    expect(text).toContain('baseline unavailable')
  })

  it('加载失败显示错误态', async () => {
    getReleaseComparison.mockRejectedValue(new Error('boom'))
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    act(() => {
      root.render(<ReleaseComparisonPanel promptKey="rag.qa" releaseId="rel-1" />)
    })
    await act(async () => { await Promise.resolve() })
    await act(async () => { await Promise.resolve() })
    expect(container.textContent).toContain('对比数据加载失败：boom')
  })
})
