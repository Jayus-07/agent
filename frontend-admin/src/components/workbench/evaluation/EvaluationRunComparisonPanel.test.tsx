import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { evaluationService } from '@/api/evaluation'
import EvaluationRunComparisonPanel from './EvaluationRunComparisonPanel'

vi.mock('@/lib/auth', () => ({ atLeast: () => true }))

let mounted: { root: Root; container: HTMLDivElement } | null = null

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })
beforeEach(() => {
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([
    { run_id: 'candidate-run', suite_version_id: 'suite-version', job_status: 'completed', total_cases: 2, completed_cases: 2, passed_cases: 2 },
    { run_id: 'baseline-run', suite_version_id: 'suite-version', job_status: 'completed', total_cases: 2, completed_cases: 2, passed_cases: 1 },
  ])
  vi.spyOn(evaluationService, 'listV2Experiments').mockResolvedValue([])
  vi.spyOn(evaluationService, 'getV2RunCaseDetail').mockImplementation(async runId => ({
    run_id: runId,
    case_id: 'case-1',
    case_revision: 1,
    result: {
      status: runId === 'candidate-run' ? 'fail' : 'pass',
      expected: { reference_answer: '标准答案' },
      actual: { answer: runId === 'candidate-run' ? '候选回答' : '基线回答' },
      metrics: { accuracy: runId === 'candidate-run' ? 0.3 : 0.9 },
      duration_ms: 20,
    },
    trace_id: `trace-${runId}`,
    cost_status: 'not_reported_by_legacy_runner',
  }))
  vi.spyOn(evaluationService, 'compareV2Runs').mockResolvedValue({
    comparable: true,
    reasons: [],
    baseline_run_id: 'baseline-run',
    candidate_run_id: 'candidate-run',
    metrics: [],
    pass_rate_inference: { sample_count: 1, excluded_count: 0, discordant_count: 1, candidate_only_pass: 0, baseline_only_pass: 1, pass_rate_delta: -1, ci95_lower: -1, ci95_upper: 0.5866, p_value: 1, p_value_method: 'exact_mcnemar_two_sided', confidence_interval_method: 'paired_wilson_95' },
    cases: [{
      case_id: 'case-1',
      baseline: 'pass',
      candidate: 'fail',
      scores: { baseline: { accuracy: 0.9 }, candidate: { accuracy: 0.3 } },
    }],
    slices: [{
      field: 'category',
      value: 'faq',
      sample_count: 2,
      baseline_pass_rate: 0.5,
      candidate_pass_rate: 1,
      pass_rate_delta: 0.5,
      metrics: [],
    }],
    slices_truncated: 0,
  })
})
afterEach(() => {
  mounted?.root.unmount()
  mounted?.container.remove()
  mounted = null
  vi.restoreAllMocks()
})

describe('EvaluationRunComparisonPanel', () => {
  it('呈现服务端固定标签的分组通过率差异', async () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted = { root, container }
    await act(async () => {
      root.render(<EvaluationRunComparisonPanel />)
      await new Promise(resolve => setTimeout(resolve, 0))
    })

    const compare = Array.from(container.querySelectorAll('button')).find(button => button.textContent === '比较实验')
    expect(compare?.disabled).toBe(false)
    await act(async () => { compare?.click(); await new Promise(resolve => setTimeout(resolve, 0)) })

    expect(evaluationService.compareV2Runs).toHaveBeenCalledWith('baseline-run', 'candidate-run')
    expect(container.textContent).toContain('切片对比')
    expect(container.textContent).toContain('配对通过率不确定性')
    expect(container.textContent).toContain('双侧精确 p 值')
    expect(container.textContent).toContain('faq')
    expect(container.textContent).toContain('+50%')
    expect(container.textContent).toContain('结果仅供探索，不参与 Gate')

    const detail = container.querySelector('button[aria-label="查看用例 case-1 的 A/B 回答差异"]') as HTMLButtonElement
    expect(detail).not.toBeNull()
    await act(async () => { detail.click(); await new Promise(resolve => setTimeout(resolve, 0)) })
    expect(evaluationService.getV2RunCaseDetail).toHaveBeenCalledWith('baseline-run', 'case-1')
    expect(evaluationService.getV2RunCaseDetail).toHaveBeenCalledWith('candidate-run', 'case-1')
    expect(container.textContent).toContain('基线回答')
    expect(container.textContent).toContain('候选回答')
    expect(container.textContent).toContain('共同问题期望与证据')
  })
})
