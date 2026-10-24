import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { evaluationService, type EvaluationV2Candidate } from '@/api/evaluation'
import EvaluationV2ReviewPanel from './EvaluationV2ReviewPanel'

const candidate = {
  candidate_id: 'candidate-1',
  payload_json: {
    input: { query: '如何申请年假？' },
    proposed_facts: [{ behavior: 'answer', reference_answer: '提前申请', required_facts: ['提前申请'] }],
    proposed_evidence: [{ kb_id: 'kb_policy', doc_id: 'doc-1', doc_version: 'v2', quote: '提前三个工作日提交申请。' }],
    source_ref: { dataset_key: 'support_golden', module: 'rag' },
  },
  review_status: 'in_review', revision: 4, promoted_case_id: null, created_by: 'user:bob', created_at: '2026-10-24T00:00:00Z',
} satisfies EvaluationV2Candidate

let mounted: { root: Root; container: HTMLDivElement } | null = null
beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })
beforeEach(() => {
  vi.spyOn(evaluationService, 'listV2Candidates').mockResolvedValueOnce([candidate]).mockResolvedValue([])
  vi.spyOn(evaluationService, 'reviewV2Candidate').mockResolvedValue(candidate)
})
afterEach(() => {
  mounted?.root.unmount()
  mounted?.container.remove()
  mounted = null
  vi.restoreAllMocks()
})

describe('EvaluationV2ReviewPanel', () => {
  it('审核时提交服务端 CAS 修订和人工确认的标准事实、来源证据', async () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted = { root, container }
    await act(async () => { root.render(<EvaluationV2ReviewPanel />); await Promise.resolve() })

    const rationale = container.querySelector('textarea[required]') as HTMLTextAreaElement
    await act(async () => {
      const valueSetter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set
      valueSetter?.call(rationale, '核对当前制度原文')
      rationale.dispatchEvent(new Event('input', { bubbles: true }))
    })
    const approve = Array.from(container.querySelectorAll('button')).find(button => button.textContent === '确认事实并批准')
    expect(approve?.disabled).toBe(false)
    await act(async () => { approve?.click(); await new Promise(resolve => setTimeout(resolve, 0)) })

    expect(evaluationService.reviewV2Candidate).toHaveBeenCalledWith('candidate-1', expect.objectContaining({
      expected_revision: 4,
      verdict: 'approved',
      rationale: '核对当前制度原文',
      expected: expect.objectContaining({ reference_answer: '提前申请' }),
      evidence: [{ kb_id: 'kb_policy', doc_id: 'doc-1', doc_version: 'v2', quote: '提前三个工作日提交申请。' }],
    }))
  })

  it('非 RAG 候选保留模块标准字段且不显示知识库证据表单', async () => {
    const sqlCandidate: EvaluationV2Candidate = {
      ...candidate,
      candidate_id: 'candidate-sql',
      payload_json: {
        ...candidate.payload_json,
        proposed_facts: [{
          gold_sql: 'SELECT COUNT(*) FROM product.products',
          expected_tables: ['product.products'],
          should_reject: false,
        }],
        proposed_evidence: [],
        source_ref: { dataset_key: 'sql_golden', module: 'sql' },
      },
    }
    const listCandidates = vi.mocked(evaluationService.listV2Candidates)
    listCandidates.mockReset().mockResolvedValueOnce([sqlCandidate]).mockResolvedValue([])
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted = { root, container }
    await act(async () => { root.render(<EvaluationV2ReviewPanel />); await Promise.resolve() })

    expect(container.querySelector('textarea[aria-label="原文证据锚点 JSON"]')).toBeNull()
    const rationale = container.querySelector('textarea[required]') as HTMLTextAreaElement
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set?.call(rationale, '核对 SQL 预期表和查询语义')
      rationale.dispatchEvent(new Event('input', { bubbles: true }))
    })
    const approve = Array.from(container.querySelectorAll('button')).find(button => button.textContent === '确认事实并批准')
    await act(async () => { approve?.click(); await new Promise(resolve => setTimeout(resolve, 0)) })
    expect(evaluationService.reviewV2Candidate).toHaveBeenCalledWith('candidate-sql', expect.objectContaining({
      expected: expect.objectContaining({ expected_tables: ['product.products'], should_reject: false, gold_sql: 'SELECT COUNT(*) FROM product.products' }),
      evidence: [],
    }))
  })
})
