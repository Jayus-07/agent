import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import DatasetsTab from './DatasetsTab'

const mocks = vi.hoisted(() => ({
  listV2Datasets: vi.fn(),
  listV2Candidates: vi.fn(),
  listV2DatasetVersions: vi.fn(),
  listFeedbackCandidates: vi.fn(),
  reviewFeedbackCandidate: vi.fn(),
  promoteFeedbackCandidate: vi.fn(),
  listKbAuthority: vi.fn(),
}))

vi.mock('@/api/evaluation', () => ({ evaluationService: {
  listV2Datasets: mocks.listV2Datasets,
  listV2Candidates: mocks.listV2Candidates,
  listV2DatasetVersions: mocks.listV2DatasetVersions,
} }))
vi.mock('@/api/feedbackCandidates', () => ({
  canPromoteCandidate: (status: string) => status === 'approved',
  listFeedbackCandidates: mocks.listFeedbackCandidates,
  reviewFeedbackCandidate: mocks.reviewFeedbackCandidate,
  promoteFeedbackCandidate: mocks.promoteFeedbackCandidate,
}))
vi.mock('@/api/knowledge', () => ({ listKbAuthority: mocks.listKbAuthority }))
vi.mock('@/api/client', () => ({ createIdempotencyKey: () => 'test-request-id' }))
vi.mock('./EvidenceSearchPicker', () => ({ default: () => null }))

const dataset = {
  dataset_id: 'dataset-1', dataset_key: 'support-faq', name: '客服 FAQ', domain: 'cs',
  case_count: 1, version_count: 1, latest_version: '1.0.0', latest_status: 'published', updated_at: null,
}
const v2Candidates = [
  { candidate_id: 'feedback-candidate-1', source_kind: 'feedback', payload_json: { input: { query: '反馈问题' }, proposed_facts: [], proposed_evidence: [] }, review_status: 'in_review', revision: 1, created_by: 'admin', created_at: '' },
  { candidate_id: 'manual-candidate-1', source_kind: 'manual', payload_json: { input: { query: '手工问题' }, proposed_facts: [], proposed_evidence: [] }, review_status: 'in_review', revision: 1, created_by: 'admin', created_at: '' },
]
const pendingFeedback = {
  candidate_id: 'legacy-feedback-1', status: 'pending', trace_id: 'trace-1', module: 'cs', summary: '反馈里的问题',
  reason: '回答未解决问题', correction_text: '使用正确政策', expected_answer: '正确答案', created_at: '',
}
const secondPendingFeedback = {
  candidate_id: 'legacy-feedback-2', status: 'pending', trace_id: 'trace-2', module: 'rag', summary: '第二条反馈候选', created_at: '',
}

function mount() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  return { container, root }
}

beforeEach(() => {
  mocks.listV2Datasets.mockResolvedValue([dataset])
  mocks.listV2Candidates.mockResolvedValue(v2Candidates)
  mocks.listV2DatasetVersions.mockResolvedValue([])
  mocks.listFeedbackCandidates.mockResolvedValue({ items: [pendingFeedback, secondPendingFeedback] })
  mocks.reviewFeedbackCandidate.mockImplementation(async (id: string, decision: string) => ({
    ...pendingFeedback, candidate_id: id, status: decision === 'approve' ? 'approved' : 'rejected',
  }))
  mocks.promoteFeedbackCandidate.mockResolvedValue({ status: 'promoted', case_id: 'case-1', appended: true })
  mocks.listKbAuthority.mockResolvedValue({ knowledge_bases: [] })
})

afterEach(() => {
  vi.clearAllMocks()
})

it('只在反馈候选 API 成功后更新状态，并允许 approved 候选晋升', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  const { container, root } = mount()
  try {
    await act(async () => { root.render(<DatasetsTab onGoRuns={() => undefined} />) })
    expect(container.textContent).toContain('source=feedback')
    expect(container.textContent).toContain('反馈里的问题')

    const approve = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.trim() === '批准')
    expect(approve).toBeDefined()
    await act(async () => { approve!.click() })
    expect(mocks.reviewFeedbackCandidate).toHaveBeenCalledWith('legacy-feedback-1', 'approve', '')
    expect(container.textContent).toContain('反馈候选已批准')

    const reject = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.trim() === '拒绝')
    expect(reject).toBeDefined()
    await act(async () => { reject!.click() })
    expect(mocks.reviewFeedbackCandidate).toHaveBeenCalledWith('legacy-feedback-2', 'reject', '')

    const statusFilter = container.querySelector('[aria-label="反馈候选状态筛选"]') as HTMLSelectElement
    await act(async () => { statusFilter.value = 'all'; statusFilter.dispatchEvent(new Event('change', { bubbles: true })) })
    const promote = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.trim() === '晋升为评测用例')
    expect(promote).toBeDefined()
    await act(async () => { promote!.click() })
    expect(mocks.promoteFeedbackCandidate).toHaveBeenCalledWith('legacy-feedback-1')
    expect(container.textContent).toContain('已追加为评测用例：case-1')
  } finally {
    await act(async () => root.unmount())
    container.remove()
  }
})

it('数据集编辑视图可按 source_kind=feedback 筛选候选', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  const { container, root } = mount()
  try {
    await act(async () => { root.render(<DatasetsTab onGoRuns={() => undefined} />) })
    const edit = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('编辑'))
    expect(edit).toBeDefined()
    await act(async () => { edit!.click() })
    expect(container.textContent).toContain('feedback')
    expect(container.textContent).toContain('manual')

    const sourceFilter = container.querySelector('[aria-label="按候选来源筛选"]') as HTMLSelectElement
    await act(async () => { sourceFilter.value = 'feedback'; sourceFilter.dispatchEvent(new Event('change', { bubbles: true })) })
    expect(container.textContent).toContain('feedback')
    expect(container.textContent).not.toContain('manual')
  } finally {
    await act(async () => root.unmount())
    container.remove()
  }
})
