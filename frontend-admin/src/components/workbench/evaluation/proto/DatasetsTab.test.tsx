import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import DatasetsTab from './DatasetsTab'

const mocks = vi.hoisted(() => ({
  listV2Datasets: vi.fn(),
  listV2Candidates: vi.fn(),
  listV2CandidateReviews: vi.fn(),
  reviewV2Candidate: vi.fn(),
  listV2DatasetVersions: vi.fn(),
  publishV2DatasetVersion: vi.fn(),
  listV2Evaluators: vi.fn(),
  createV2Suite: vi.fn(),
  publishV2Suite: vi.fn(),
  listFeedbackCandidates: vi.fn(),
  reviewFeedbackCandidate: vi.fn(),
  promoteFeedbackCandidate: vi.fn(),
  listKbAuthority: vi.fn(),
  freeze: vi.fn(),
  synthesize: vi.fn(),
  generatedDatasetKeys: [] as string[],
}))

vi.mock('@/api/evaluation', () => ({ evaluationService: {
  listV2Datasets: mocks.listV2Datasets,
  listV2Candidates: mocks.listV2Candidates,
  listV2CandidateReviews: mocks.listV2CandidateReviews,
  reviewV2Candidate: mocks.reviewV2Candidate,
  listV2DatasetVersions: mocks.listV2DatasetVersions,
  publishV2DatasetVersion: mocks.publishV2DatasetVersion,
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
vi.mock('@/api/evaluation-assets', () => ({ evaluationAssets: {
  freeze: mocks.freeze,
  synthesize: mocks.synthesize,
} }))
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
  mocks.listV2Candidates.mockImplementation(async (status: string) => status === 'in_review' ? v2Candidates : [])
  mocks.listV2CandidateReviews.mockResolvedValue([])
  mocks.reviewV2Candidate.mockResolvedValue({ review_status: 'approved' })
  mocks.listV2DatasetVersions.mockResolvedValue([])
  mocks.publishV2DatasetVersion.mockResolvedValue({ dataset_version_id: 'dataset-version-2', version: '1.1.0' })
  mocks.listV2Evaluators.mockResolvedValue([])
  mocks.createV2Suite.mockResolvedValue({ suite_version_id: 'suite-version-1', status: 'draft' })
  mocks.publishV2Suite.mockResolvedValue({ suite_version_id: 'suite-version-1', status: 'published', snapshot_hash: 'suite-hash' })
  mocks.listFeedbackCandidates.mockResolvedValue({ items: [pendingFeedback, secondPendingFeedback] })
  mocks.reviewFeedbackCandidate.mockImplementation(async (id: string, decision: string) => ({
    ...pendingFeedback, candidate_id: id, status: decision === 'approve' ? 'approved' : 'rejected',
  }))
  mocks.promoteFeedbackCandidate.mockResolvedValue({ status: 'promoted', case_id: 'case-1', appended: true })
  mocks.listKbAuthority.mockResolvedValue({ knowledge_bases: [] })
  mocks.freeze.mockImplementation(async (kbId: string) => ({ snapshot_id: `${kbId.padEnd(24, '0').slice(0, 24)}` }))
  mocks.synthesize.mockImplementation(async (_snapshotId: string, manifest: { dataset_key: string }) => {
    mocks.generatedDatasetKeys.push(manifest.dataset_key)
    return { task_id: `task-${mocks.generatedDatasetKeys.length}`, run_id: `task-${mocks.generatedDatasetKeys.length}`, status: 'SUCCESS', progress: '评测完成' }
  })
  mocks.generatedDatasetKeys = []
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

it('Ragas候选需完成质量清单、必需事实和人工理由后才能批准', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  mocks.listV2Datasets.mockResolvedValue([{ ...dataset, domain: 'rag' }])
  const ragasCandidate = {
    candidate_id: 'ragas-candidate-1',
    source_kind: 'document_synthesized',
    payload_json: {
      input: { query: '结合规则甲和规则乙，最终应该如何处理？' },
      proposed_facts: [{ reference_answer: '应按两条规则共同处理。', required_facts: [] }],
      proposed_evidence: [
        { kb_id: 'kb-test', doc_id: 'doc-1', doc_version: 'v1', quote: '规则甲原文。' },
        { kb_id: 'kb-test', doc_id: 'doc-2', doc_version: 'v1', quote: '规则乙原文。' },
      ],
      metadata: {
        dataset_key: 'support-faq',
        generation_method: 'ragas_mixed',
        query_type: 'multi_hop_specific',
      },
    },
    review_status: 'in_review', revision: 1, created_by: 'admin', created_at: '',
  }
  mocks.listV2Candidates.mockImplementation(async (status: string) => status === 'in_review' ? [ragasCandidate] : [])
  mocks.reviewV2Candidate.mockImplementation(async () => {
    mocks.listV2Candidates.mockResolvedValue([])
    return { review_status: 'approved' }
  })
  const { container, root } = mount()
  try {
    await act(async () => { root.render(<DatasetsTab onGoRuns={() => undefined} />) })
    const edit = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('编辑'))
    expect(edit).toBeDefined()
    await act(async () => { edit!.click() })
    for (let index = 0; index < 3; index += 1) {
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
    }

    const approve = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.trim() === '批准进草稿') as HTMLButtonElement
    expect(approve.disabled).toBe(true)
    const facts = container.querySelector('[aria-label="必需事实"]') as HTMLInputElement
    const setValue = (element: HTMLInputElement | HTMLTextAreaElement, value: string) => {
      const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
      Object.getOwnPropertyDescriptor(prototype, 'value')?.set?.call(element, value)
      element.dispatchEvent(new Event('input', { bubbles: true }))
      element.dispatchEvent(new Event('change', { bubbles: true }))
    }
    await act(async () => {
      setValue(facts, '规则甲要求核验资格，规则乙要求提交凭证')
      const rationale = container.querySelector('[aria-label="人工审核理由（Ragas候选）"]') as HTMLTextAreaElement
      setValue(rationale, '已逐条核对两段原文，问题清楚且不重复，答案中的两项事实都能分别从证据推出。')
      for (const label of [
        '问题清晰、无歧义且与现有题不重复',
        '参考答案中的事实都能从原文推出',
        '必需事实已补齐，能据此判定答案质量',
        '证据覆盖答案的全部关键事实',
        '题型与实际推理步骤一致',
      ]) {
        (container.querySelector(`[aria-label="质量核验：${label}"]`) as HTMLInputElement).click()
      }
    })

    expect(approve.disabled).toBe(true)
    await act(async () => {
      const factOneSameEvidence = container.querySelector('[aria-label="事实 1 的证据引用 1（doc-1）"]') as HTMLInputElement
      const factTwoSameEvidence = container.querySelector('[aria-label="事实 2 的证据引用 1（doc-1）"]') as HTMLInputElement
      factOneSameEvidence.click()
      factTwoSameEvidence.click()
    })
    expect(approve.disabled).toBe(true)
    await act(async () => {
      const factTwoSameEvidence = container.querySelector('[aria-label="事实 2 的证据引用 1（doc-1）"]') as HTMLInputElement
      const factTwoIndependentEvidence = container.querySelector('[aria-label="事实 2 的证据引用 2（doc-2）"]') as HTMLInputElement
      factTwoSameEvidence.click()
      factTwoIndependentEvidence.click()
    })
    expect(approve.disabled).toBe(false)
    await act(async () => { approve.click(); await new Promise(resolve => setTimeout(resolve, 0)) })
    expect(mocks.reviewV2Candidate).toHaveBeenCalledWith('ragas-candidate-1', expect.objectContaining({
      verdict: 'approved',
      rationale: '已逐条核对两段原文，问题清楚且不重复，答案中的两项事实都能分别从证据推出。',
      quality_checks: {
        question_clear_and_non_duplicate: true,
        answer_facts_supported: true,
        required_facts_complete: true,
        evidence_covers_answer: true,
        query_type_matches_reasoning: true,
      },
      fact_evidence_bindings: [
        { fact_index: 0, evidence_indices: [0] },
        { fact_index: 1, evidence_indices: [1] },
      ],
    }))
  } finally {
    await act(async () => root.unmount())
    container.remove()
  }
})

it('拒绝Ragas候选需说明具体理由并保留当次证据引用', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  mocks.listV2Datasets.mockResolvedValue([{ ...dataset, domain: 'rag' }])
  const rejectedCandidate = {
    candidate_id: 'ragas-rejected-candidate',
    source_kind: 'document_synthesized',
    payload_json: {
      input: { query: '某项要求是什么？' },
      proposed_facts: [{ reference_answer: '按来源原文处理。', required_facts: [] }],
      proposed_evidence: [{
        kb_id: 'kb-test', doc_id: 'doc-1', doc_version: 'v1', quote: '来源原文。',
      }],
      metadata: {
        dataset_key: 'support-faq',
        generation_method: 'ragas_mixed',
        query_type: 'multi_hop_specific',
      },
    },
    review_status: 'in_review', revision: 1, created_by: 'admin', created_at: '',
  }
  let rejected = false
  mocks.listV2Candidates.mockImplementation(async (status: string) => {
    if (status === 'in_review' && !rejected) return [rejectedCandidate]
    if (status === 'rejected' && rejected) return [{ ...rejectedCandidate, review_status: 'rejected' }]
    return []
  })
  mocks.reviewV2Candidate.mockImplementation(async () => {
    rejected = true
    return { review_status: 'rejected' }
  })
  const { container, root } = mount()
  try {
    await act(async () => { root.render(<DatasetsTab onGoRuns={() => undefined} />) })
    const edit = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('编辑'))
    expect(edit).toBeDefined()
    await act(async () => { edit!.click() })
    for (let index = 0; index < 3; index += 1) {
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
    }

    const reject = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.trim() === '拒绝候选') as HTMLButtonElement
    expect(reject.disabled).toBe(true)
    const rationale = container.querySelector('[aria-label="人工审核理由（Ragas候选）"]') as HTMLTextAreaElement
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set
    await act(async () => {
      setter?.call(rationale, '该问题可由单条来源直接回答，现有两段证据重复同一事实，不符合多跳题型。')
      rationale.dispatchEvent(new Event('input', { bubbles: true }))
      rationale.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(reject.disabled).toBe(false)
    await act(async () => { reject.click(); await new Promise(resolve => setTimeout(resolve, 0)) })
    expect(mocks.reviewV2Candidate).toHaveBeenCalledWith('ragas-rejected-candidate', expect.objectContaining({
      verdict: 'rejected',
      rationale: '该问题可由单条来源直接回答，现有两段证据重复同一事实，不符合多跳题型。',
      evidence: [{
        kb_id: 'kb-test', doc_id: 'doc-1', doc_version: 'v1', quote: '来源原文。',
      }],
    }))
  } finally {
    await act(async () => root.unmount())
    container.remove()
  }
})

it('数据集可查回已完成审核的理由、核验项和原文证据', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  const approvedCandidate = {
    candidate_id: 'approved-ragas-candidate',
    source_kind: 'document_synthesized',
    payload_json: {
      input: { query: '结合两条规则应该如何处理？' },
      metadata: { dataset_key: 'support-faq', generation_method: 'ragas_mixed' },
    },
    review_status: 'approved', revision: 2, created_by: 'admin', created_at: '2026-11-12T04:00:00Z',
  }
  mocks.listV2Candidates.mockImplementation(async (status: string) => (
    status === 'in_review' ? [] : status === 'approved' ? [approvedCandidate] : []
  ))
  mocks.listV2CandidateReviews.mockResolvedValue([{
    annotation_id: 'annotation-ragas-1',
    candidate_revision: 2,
    reviewer_id: 'user:reviewer',
    verdict: 'approved',
    changed_fields: {
      quality_checks: {
        question_clear_and_non_duplicate: true,
        answer_facts_supported: true,
        required_facts_complete: true,
        evidence_covers_answer: true,
        query_type_matches_reasoning: true,
      },
      fact_evidence_bindings: [
        { fact_index: 0, fact: '规则甲', anchor_ids: ['chunk-1'] },
        { fact_index: 1, fact: '规则乙', anchor_ids: ['chunk-2'] },
      ],
    },
    evidence_json: [{ doc_id: 'policy-1', doc_version: 'v3', anchor_id: 'chunk-1', quote: '两条规则的原文内容。' }],
    rationale: '已核对问题、答案和两段原文，必需事实完整且题型与推理过程相符。',
    created_at: '2026-11-12T04:01:00Z',
  }, {
    annotation_id: 'annotation-ragas-legacy',
    candidate_revision: 1,
    reviewer_id: 'user:previous-reviewer',
    verdict: 'approved',
    changed_fields: {},
    evidence_json: [{ doc_id: 'policy-legacy', doc_version: 'v1', anchor_id: 'chunk-legacy' }],
    rationale: '旧审核记录只保存了锚点元数据。',
    created_at: '2026-11-12T03:59:00Z',
  }])
  const { container, root } = mount()
  try {
    await act(async () => { root.render(<DatasetsTab onGoRuns={() => undefined} />) })
    const edit = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('编辑'))
    expect(edit).toBeDefined()
    await act(async () => { edit!.click() })
    for (let index = 0; index < 3; index += 1) {
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
    }

    expect(container.textContent).toContain('审核记录')
    expect(mocks.listV2Candidates).toHaveBeenCalledWith('approved', 200, 'support-faq')
    const show = container.querySelector('[aria-label="查看审核记录 approved-ragas-candidate"]') as HTMLButtonElement
    expect(show).toBeDefined()
    await act(async () => { show.click(); await new Promise(resolve => setTimeout(resolve, 0)) })

    expect(mocks.listV2CandidateReviews).toHaveBeenCalledWith('approved-ragas-candidate')
    expect(container.textContent).toContain('user:reviewer')
    expect(container.textContent).toContain('答案中的事实都能从原文推出')
    expect(container.textContent).toContain('policy-1 · v3 · 锚点 chunk-1')
    expect(container.textContent).toContain('两条规则的原文内容。')
    expect(container.textContent).toContain('事实与已核验锚点对应')
    expect(container.textContent).toContain('事实 2：规则乙 → chunk-2')
    expect(container.textContent).toContain('已核验锚点；本记录未保存原文引用文本')
    expect(container.textContent).toContain('policy-legacy · v1 · 锚点 chunk-legacy')
    expect(container.textContent).toContain('审核时未保存引用文本。')
    expect(container.textContent).toContain('已核对问题、答案和两段原文')
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

it('旅游方案明确提示离线和 fake Provider 执行边界', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  for (const [domain, warning] of [
    ['travel', '当前 V2 旅游 Runner 按离线模式执行，不调用真实地图或预订供应商'],
    ['travel-provider', '当前仅执行 fake Provider 探针，不触发真实第三方请求'],
  ] as const) {
    const travelDataset = { ...dataset, domain }
    mocks.listV2Datasets.mockResolvedValue([travelDataset])
    mocks.listV2DatasetVersions.mockResolvedValue([{
      dataset_version_id: `${domain}-version-1`, dataset_id: travelDataset.dataset_id, version: '1.0.0', status: 'published',
      content_hash: `${domain}-hash-123456`, case_count: domain === 'travel' ? 34 : 8,
    }])
    const { container, root } = mount()
    try {
      await act(async () => { root.render(<DatasetsTab onGoRuns={() => undefined} />) })
      const configure = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('配置评测'))
      expect(configure).toBeDefined()
      await act(async () => { configure!.click() })
      for (let index = 0; index < 3; index += 1) {
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
      }

      expect(container.textContent).toContain(warning)
      if (domain === 'travel-provider') {
        expect(container.textContent).toContain('通过不代表真实供应商链路已验收')
      } else {
        expect(container.textContent).toContain('通过只说明离线评测通过')
      }
    } finally {
      await act(async () => root.unmount())
      container.remove()
    }
  }
})

it('从候选 payload 的已核验修订号发布数据集版本', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  mocks.listV2Candidates.mockImplementation(async (status: string) => status === 'approved' ? [{
    candidate_id: 'approved-candidate-1',
    source_kind: 'document_synthesized',
    payload_json: {
      input: { query: '加班如何申请？' },
      metadata: { dataset_key: 'support-faq' },
      verified_case_revision: 1,
    },
    review_status: 'approved',
    revision: 2,
    promoted_case_id: 'case-1',
    // list_candidates 接口当前将核验后的修订号保存在 payload_json 中。
    created_by: 'admin',
    created_at: '',
  }] : [])
  mocks.listV2DatasetVersions.mockResolvedValue([{
    dataset_version_id: 'dataset-version-1', dataset_id: dataset.dataset_id, version: '1.0.0', status: 'published',
    content_hash: 'dataset-hash', case_count: 1,
  }])

  const { container, root } = mount()
  try {
    await act(async () => { root.render(<DatasetsTab onGoRuns={() => undefined} />) })
    const history = Array.from(container.querySelectorAll('button')).find(button => button.title === '查看版本历史')
    expect(history).toBeDefined()
    await act(async () => { history!.click() })
    for (let index = 0; index < 3; index += 1) {
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
    }

    const publish = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.trim() === '发布已批准用例')
    expect(publish).toBeDefined()
    await act(async () => { publish!.click(); await new Promise(resolve => setTimeout(resolve, 0)) })

    expect(mocks.publishV2DatasetVersion).toHaveBeenCalledWith(dataset.dataset_id, {
      version: '1.1.0', items: [{ case_id: 'case-1', revision: 1 }],
    })
    expect(container.textContent).toContain('已发布新版本 v1.1.0（1 条）')
  } finally {
    await act(async () => root.unmount())
    container.remove()
  }
})

it('同一知识库拆批时复用数据集键并分配不同导入版本', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  mocks.listKbAuthority.mockResolvedValue({ knowledge_bases: [
    { kb_id: 'kb-alpha', name: '知识库 A', domain: 'rag', owner_depts: [], audience: 'test', deprecated: false, read_only: false, active: true, version: 1, doc_count: 1 },
    { kb_id: 'kb-beta', name: '知识库 B', domain: 'rag', owner_depts: [], audience: 'test', deprecated: false, read_only: false, active: true, version: 1, doc_count: 1 },
  ] })
  mocks.listV2Candidates.mockImplementation(async (status: string) => status === 'in_review'
    ? [...new Set(mocks.generatedDatasetKeys)].map((datasetKey, index) => ({
      candidate_id: `generated-candidate-${index}`,
      source_kind: 'document_synthesized',
      payload_json: { input: { query: '测试问题' }, metadata: { dataset_key: datasetKey }, proposed_facts: [], proposed_evidence: [] },
      review_status: 'in_review', revision: 1, created_by: 'admin', created_at: '',
    }))
    : [])

  const { container, root } = mount()
  try {
    await act(async () => { root.render(<DatasetsTab onGoRuns={() => undefined} />) })
    const open = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.trim() === '从知识库生成')
    expect(open).toBeDefined()
    await act(async () => { open!.click() })
    expect((container.querySelector('[aria-label="单跳具体题数"]') as HTMLInputElement).value).toBe('3')
    expect((container.querySelector('[aria-label="多跳抽象题数"]') as HTMLInputElement).value).toBe('1')
    expect((container.querySelector('[aria-label="多跳具体题数"]') as HTMLInputElement).value).toBe('1')
    const abstractType = container.querySelector('[aria-label="启用多跳抽象"]') as HTMLInputElement
    const specificType = container.querySelector('[aria-label="启用多跳具体"]') as HTMLInputElement
    await act(async () => { abstractType.click() })
    expect(specificType.disabled).toBe(true)
    await act(async () => { abstractType.click() })
    expect(specificType.disabled).toBe(false)
    for (const kbId of ['kb-alpha', 'kb-beta']) {
      const checkbox = container.querySelector(`[aria-label="选择知识库 ${kbId}"]`) as HTMLInputElement
      await act(async () => { checkbox.click() })
    }
    const twenty = Array.from(container.querySelectorAll('[role="radio"]')).find(button => button.textContent?.trim() === '20 题') as HTMLButtonElement
    await act(async () => { twenty.click() })
    expect(container.textContent).toContain('计划提交 4 个任务；产出 2 个独立数据集。')

    const start = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.trim() === '开始生成')
    expect(start).toBeDefined()
    await act(async () => { start!.click(); await new Promise(resolve => setTimeout(resolve, 0)) })

    const calls = mocks.synthesize.mock.calls
    expect(calls).toHaveLength(4)
    for (const kbId of ['kb-alpha', 'kb-beta']) {
      const kbCalls = calls.filter(([snapshotId]) => String(snapshotId).startsWith(kbId))
      expect(kbCalls).toHaveLength(2)
      expect(kbCalls.map(([, manifest, count]) => [manifest.dataset_key, manifest.version, count])).toEqual([
        [expect.stringContaining(kbId), '1.0.0', 5],
        [expect.stringContaining(kbId), '1.0.1', 5],
      ])
      expect(kbCalls[0][1].dataset_key).toBe(kbCalls[1][1].dataset_key)
      expect(kbCalls.every(([, , count, queryCounts]) => count === Object.values(queryCounts).reduce<number>((sum, amount) => sum + Number(amount), 0))).toBe(true)
      expect(kbCalls[0][4]).toEqual(kbCalls[1][4])
      expect(Number(kbCalls[0][4].multi_hop_abstract) + Number(kbCalls[0][4].multi_hop_specific)).toBeGreaterThan(0)
    }
    const requestedMix = calls.reduce((sum, [, , , queryCounts]) => ({
      single_hop_specific: sum.single_hop_specific + Number(queryCounts.single_hop_specific),
      multi_hop_abstract: sum.multi_hop_abstract + Number(queryCounts.multi_hop_abstract),
      multi_hop_specific: sum.multi_hop_specific + Number(queryCounts.multi_hop_specific),
    }), { single_hop_specific: 0, multi_hop_abstract: 0, multi_hop_specific: 0 })
    expect(requestedMix).toEqual({ single_hop_specific: 10, multi_hop_abstract: 5, multi_hop_specific: 5 })
    expect(container.textContent).not.toContain('部分批次失败')
  } finally {
    await act(async () => root.unmount())
    container.remove()
  }
})

it('知识库生成失败时展示任务接口返回的具体原因', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  const detail = '同一数据集版本已有不同内容，请增加版本号后再提交'
  mocks.listKbAuthority.mockResolvedValue({ knowledge_bases: [
    { kb_id: 'kb-alpha', name: '知识库 A', domain: 'rag', owner_depts: [], audience: 'test', deprecated: false, read_only: false, active: true, version: 1, doc_count: 1 },
  ] })
  mocks.listV2Candidates.mockImplementation(async (status: string) => {
    const datasetKey = mocks.generatedDatasetKeys[0]
    return status === 'in_review' && datasetKey ? [{
      candidate_id: 'generated-candidate-1',
      source_kind: 'document_synthesized',
      payload_json: { input: { query: '测试问题' }, metadata: { dataset_key: datasetKey }, proposed_facts: [], proposed_evidence: [] },
      review_status: 'in_review', revision: 1, created_by: 'admin', created_at: '',
    }] : []
  })
  mocks.synthesize.mockImplementationOnce(async (_snapshotId: string, manifest: { dataset_key: string }) => {
    mocks.generatedDatasetKeys.push(manifest.dataset_key)
    return { task_id: 'failed-task', run_id: 'failed-task', status: 'FAILED', progress: '不可重试错误（validation_error）', error: detail }
  })

  const { container, root } = mount()
  try {
    await act(async () => { root.render(<DatasetsTab onGoRuns={() => undefined} />) })
    const open = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.trim() === '从知识库生成')
    await act(async () => { open!.click() })
    const checkbox = container.querySelector('[aria-label="选择知识库 kb-alpha"]') as HTMLInputElement
    await act(async () => { checkbox.click() })
    const start = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.trim() === '开始生成')
    await act(async () => { start!.click(); await new Promise(resolve => setTimeout(resolve, 0)) })

    expect(container.textContent).toContain(detail)
    expect(container.textContent).toContain('部分批次失败')
  } finally {
    await act(async () => root.unmount())
    container.remove()
  }
})
