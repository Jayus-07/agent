import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { expect, it, vi } from 'vitest'
import { evaluationService } from '@/api/evaluation'
import CompareTab from './CompareTab'

vi.mock('@/lib/auth', () => ({ atLeast: () => true }))
vi.mock('@/components/workbench/evaluation/EvaluationHumanReviewButton', () => ({
  default: ({ runId, caseId }: { runId: string; caseId: string }) => <div data-testid="human-review" data-run-id={runId} data-case-id={caseId} />,
}))

it('用例详情挂载人工评审，并明确标记不进入自动门禁', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([
    { run_id: 'baseline-run', suite_version_id: 'suite', suite_name: 'suite', suite_key: 'suite', job_status: 'completed', total_cases: 1, completed_cases: 1, passed_cases: 0 },
    { run_id: 'candidate-run', suite_version_id: 'suite', suite_name: 'suite', suite_key: 'suite', job_status: 'completed', total_cases: 1, completed_cases: 1, passed_cases: 1 },
  ] as Awaited<ReturnType<typeof evaluationService.listV2Runs>>)
  vi.spyOn(evaluationService, 'compareV2Runs').mockResolvedValue({
    comparable: true, reasons: [], baseline_run_id: 'baseline-run', candidate_run_id: 'candidate-run', metrics: [], slices: [], slices_truncated: 0, pass_rate_inference: null,
    cases: [{ case_id: 'case-1', question: '测试问题', baseline: 'fail', candidate: 'pass', scores: { baseline: { score: 0 }, candidate: { score: 1 } } }],
  })
  vi.spyOn(evaluationService, 'getV2RunCaseDetail').mockResolvedValue({
    run_id: 'candidate-run', case_id: 'case-1', case_revision: 1, result: {
      status: 'pass', expected: { query: '测试问题', reference_answer: '期望答案' }, actual: { query: '测试问题', answer: '实际答案' }, metrics: {}, duration_ms: 100,
    }, cost_status: 'unavailable',
  })

  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } })
  try {
    await act(async () => { root.render(<QueryClientProvider client={queryClient}><CompareTab /></QueryClientProvider>) })
    const selects = container.querySelectorAll('select')
    await act(async () => {
      ;(selects[1] as HTMLSelectElement).value = 'baseline-run'
      selects[1].dispatchEvent(new Event('change', { bubbles: true }))
      ;(selects[2] as HTMLSelectElement).value = 'candidate-run'
      selects[2].dispatchEvent(new Event('change', { bubbles: true }))
    })
    const compare = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.trim() === '开始对比')
    await act(async () => { compare!.click() })
    const detail = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('详情'))
    await act(async () => { detail!.click() })
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })

    expect(container.textContent).toContain('人工评审仅作校准记录，不进入自动门禁。')
    expect(container.querySelector('[role="dialog"]')?.getAttribute('aria-label')).toBe('用例详情（对比侧）')
    const review = container.querySelector('[data-testid="human-review"]')
    expect(review?.getAttribute('data-run-id')).toBe('candidate-run')
    expect(review?.getAttribute('data-case-id')).toBe('case-1')
  } finally {
    await act(async () => root.unmount())
    queryClient.clear()
    container.remove()
    vi.restoreAllMocks()
  }
})
