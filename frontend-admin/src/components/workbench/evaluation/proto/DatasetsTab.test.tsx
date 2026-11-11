import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import DatasetsTab from './DatasetsTab'

const mocks = vi.hoisted(() => ({
  listV2Datasets: vi.fn(),
  listV2Candidates: vi.fn(),
  listV2DatasetVersions: vi.fn(),
  listV2Evaluators: vi.fn(),
  createV2Suite: vi.fn(),
  publishV2Suite: vi.fn(),
  listFeedbackCandidates: vi.fn(),
  reviewFeedbackCandidate: vi.fn(),
  promoteFeedbackCandidate: vi.fn(),
  listKbAuthority: vi.fn(),
}))

vi.mock('@/api/evaluation', () => ({ evaluationService: {
  listV2Datasets: mocks.listV2Datasets,
  listV2Candidates: mocks.listV2Candidates,
  listV2DatasetVersions: mocks.listV2DatasetVersions,
  listV2Evaluators: mocks.listV2Evaluators,
  createV2Suite: mocks.createV2Suite,
  publishV2Suite: mocks.publishV2Suite,
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
  { candidate_id: 'feedback-candidate-1', source_kind: 'feedback', payload_json: { input: { query: '反馈问题' }, proposed_facts: [], proposed_evidence: [], metadata: { dataset_key: 'support-faq' } }, review_status: 'in_review', revision: 1, created_by: 'admin', created_at: '' },
  { candidate_id: 'manual-candidate-1', source_kind: 'manual', payload_json: { input: { query: '手工问题' }, proposed_facts: [], proposed_evidence: [], metadata: { dataset_key: 'support-faq' } }, review_status: 'in_review', revision: 1, created_by: 'admin', created_at: '' },
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
  mocks.listV2Evaluators.mockResolvedValue([])
  mocks.createV2Suite.mockResolvedValue({ suite_version_id: 'suite-version-1', status: 'draft' })
  mocks.publishV2Suite.mockResolvedValue({ suite_version_id: 'suite-version-1', status: 'published', snapshot_hash: 'suite-hash' })
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

it('从数据集配置已发布版本，发布方案后将方案版本带入运行页', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  mocks.listV2Datasets.mockResolvedValue([{ ...dataset, domain: 'rag' }])
  mocks.listV2DatasetVersions.mockResolvedValue([{
    dataset_version_id: 'dataset-version-1', dataset_id: dataset.dataset_id, version: '1.0.0', status: 'published',
    content_hash: 'dataset-hash-1234567890', case_count: 1,
  }])
  mocks.listV2Evaluators.mockResolvedValue([
    { evaluator_version_id: 'answer-evaluator', evaluator_key: 'answer_quality', version: '1.0.0', evaluator_kind: 'deterministic', config_json: {}, rubric_hash: 'answer-hash', status: 'published' },
    { evaluator_version_id: 'evidence-evaluator', evaluator_key: 'evidence_quality', version: '1.0.0', evaluator_kind: 'evidence', config_json: {}, rubric_hash: 'evidence-hash', status: 'published' },
  ])
  let focusedSuiteVersion = ''
  const { container, root } = mount()
  try {
    await act(async () => { root.render(<DatasetsTab onGoRuns={suiteVersionId => { focusedSuiteVersion = suiteVersionId || '' }} />) })
    const configure = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('配置评测'))
    expect(configure).toBeDefined()
    await act(async () => { configure!.click() })
    for (let index = 0; index < 3; index += 1) {
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
    }
    expect(container.textContent).toContain('固定范围')
    expect((container.querySelector('[aria-label="答案质量评分器"]') as HTMLSelectElement).value).toBe('answer-evaluator')
    const publish = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('创建并发布方案'))
    expect(publish).toBeDefined()
    await act(async () => { publish!.click(); await new Promise(resolve => setTimeout(resolve, 0)) })
    expect(mocks.createV2Suite).toHaveBeenCalledWith(expect.objectContaining({
      module: 'rag', dataset_version_id: 'dataset-version-1', minimum_valid_samples: 1,
      evaluator_versions: [
        { evaluator_version_id: 'answer-evaluator', binding: 'answer_quality' },
        { evaluator_version_id: 'evidence-evaluator', binding: 'evidence_quality' },
      ],
    }))
    expect(mocks.publishV2Suite).toHaveBeenCalledWith('suite-version-1')
    expect(focusedSuiteVersion).toBe('suite-version-1')
  } finally {
    await act(async () => root.unmount())
    container.remove()
  }
})
