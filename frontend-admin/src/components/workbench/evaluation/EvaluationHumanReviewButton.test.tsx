import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { evaluationService } from '@/api/evaluation'
import EvaluationHumanReviewButton from './EvaluationHumanReviewButton'

let mounted: { root: Root; container: HTMLDivElement } | null = null

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })
beforeEach(() => {
  vi.spyOn(evaluationService, 'getV2HumanReviews').mockResolvedValue({
    evaluators: [{
      evaluator_version_id: 'human-evaluator-1',
      evaluator_key: 'human_quality',
      version: '1.0.0',
      rubric_hash: 'rubric-hash',
      config: { criteria: [{ key: 'accuracy', label: '事实准确', weight: 1 }] },
    }],
    reviews: [],
  })
  vi.spyOn(evaluationService, 'createV2HumanReview').mockResolvedValue({
    review_id: 'review-1',
    run_id: 'run-1',
    case_id: 'case-1',
    evaluator_version_id: 'human-evaluator-1',
    label: 'partially_correct',
    overall_score: 0.75,
    scores_json: { accuracy: 0.75 },
    rationale: '整体正确，但需要补充证据。',
    reviewed_by: 'user:alice',
    reviewed_at: '2026-10-24T00:00:00Z',
  })
})
afterEach(() => {
  mounted?.root.unmount()
  mounted?.container.remove()
  mounted = null
  vi.restoreAllMocks()
})

describe('EvaluationHumanReviewButton', () => {
  it('读取固定 Rubric，追加人工分数并说明不改变机器 Gate', async () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted = { root, container }
    await act(async () => {
      root.render(<EvaluationHumanReviewButton runId="run-1" caseId="case-1" />)
    })
    const open = Array.from(container.querySelectorAll('button')).find(item => item.textContent === '人工复核')
    await act(async () => {
      open?.click()
      await new Promise(resolve => setTimeout(resolve, 0))
    })

    const score = container.querySelector('input[aria-label="事实准确评分"]') as HTMLInputElement
    const rationale = container.querySelector('textarea[aria-label="复核理由"]') as HTMLTextAreaElement
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(score, '0.75')
      score.dispatchEvent(new Event('input', { bubbles: true }))
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(rationale, '整体正确，但需要补充证据。')
      rationale.dispatchEvent(new Event('input', { bubbles: true }))
    })
    const submit = Array.from(container.querySelectorAll('button')).find(item => item.textContent === '追加人工评分')
    await act(async () => { submit?.click(); await new Promise(resolve => setTimeout(resolve, 0)) })

    expect(evaluationService.createV2HumanReview).toHaveBeenCalledWith('run-1', 'case-1', expect.objectContaining({
      evaluator_version_id: 'human-evaluator-1',
      label: 'correct',
      scores: { accuracy: 0.75 },
      rationale: '整体正确，但需要补充证据。',
    }))
    expect(container.textContent).toContain('人工复核已追加，综合分 0.750。机器评分与 Gate 未改变。')
    expect(container.textContent).toContain('整体正确，但需要补充证据。')
  })
})
