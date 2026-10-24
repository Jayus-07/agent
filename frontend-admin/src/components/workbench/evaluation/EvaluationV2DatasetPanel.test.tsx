import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { evaluationService, type EvaluationV2Candidate, type EvaluationV2Dataset } from '@/api/evaluation'
import EvaluationV2DatasetPanel from './EvaluationV2DatasetPanel'

const dataset = {
  dataset_id: 'dataset-1', dataset_key: 'support_golden', name: '客服知识评测集', domain: 'rag', description: '', status: 'active', version_count: 0,
} satisfies EvaluationV2Dataset
const candidate = {
  candidate_id: 'candidate-1', payload_json: { input: { query: '如何申请年假？' }, source_ref: { dataset_key: 'support_golden' } },
  review_status: 'approved', revision: 2, promoted_case_id: 'case-1', created_by: 'user:alice', created_at: '2026-10-24T00:00:00Z',
} satisfies EvaluationV2Candidate
const publishedVersion = {
  dataset_version_id: 'version-1', dataset_id: 'dataset-1', version: '1.0.0', status: 'published', content_hash: 'hash', case_count: 1,
}

let mounted: { root: Root; container: HTMLDivElement } | null = null
beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })
beforeEach(() => {
  vi.spyOn(evaluationService, 'listV2Datasets').mockResolvedValue([
    dataset,
    { ...dataset, dataset_id: 'dataset-2', dataset_key: 'travel_golden', name: '旅游评测集', domain: 'travel' },
  ])
  vi.spyOn(evaluationService, 'listV2Candidates').mockResolvedValue([candidate])
  vi.spyOn(evaluationService, 'listV2DatasetVersions').mockResolvedValue([])
  vi.spyOn(evaluationService, 'publishV2DatasetVersion').mockResolvedValue({
    dataset_version_id: 'version-1', dataset_id: 'dataset-1', version: '1.0.0', status: 'published', content_hash: 'hash',
  })
})
afterEach(() => {
  mounted?.root.unmount()
  mounted?.container.remove()
  mounted = null
  vi.restoreAllMocks()
})

async function mount(onGoReview: () => void = () => undefined) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  mounted = { root, container }
  await act(async () => { root.render(<EvaluationV2DatasetPanel onGoReview={onGoReview} />); await new Promise(resolve => setTimeout(resolve, 0)) })
  return container
}

describe('EvaluationV2DatasetPanel', () => {
  it('目录按名称、标识和业务域筛选逻辑数据集', async () => {
    const container = await mount()
    const search = container.querySelector('input[aria-label="搜索数据集"]') as HTMLInputElement
    const setInputValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
    await act(async () => {
      setInputValue?.call(search, 'travel')
      search.dispatchEvent(new Event('input', { bubbles: true }))
      search.dispatchEvent(new Event('change', { bubbles: true }))
      await new Promise(resolve => setTimeout(resolve, 0))
    })
    const catalog = container.querySelector('[aria-labelledby="v2-dataset-catalog-heading"]')
    const rows = catalog?.querySelectorAll('tbody tr')
    expect(rows).toHaveLength(1)
    expect(rows?.[0].textContent).toContain('travel_golden')
  })

  it('仅把当前数据集关联且已审核的 case 发布为固定 Golden 版本', async () => {
    const container = await mount()
    const checkbox = container.querySelector('input[type="checkbox"]') as HTMLInputElement
    expect(checkbox).not.toBeNull()
    await act(async () => { checkbox.click(); await Promise.resolve() })
    const publish = Array.from(container.querySelectorAll('button')).find(button => button.textContent === '发布不可变 Golden 版本')
    expect(publish?.disabled).toBe(false)
    await act(async () => { publish?.click(); await Promise.resolve(); await Promise.resolve() })
    expect(evaluationService.publishV2DatasetVersion).toHaveBeenCalledWith('dataset-1', {
      version: '1.0.0', items: [{ case_id: 'case-1', revision: 1 }],
    })
  })

  it('相同 Case 有多个已审核候选时只发布最新 revision', async () => {
    const revisedCandidate: EvaluationV2Candidate = {
      ...candidate,
      candidate_id: 'candidate-2',
      payload_json: { ...candidate.payload_json, verified_case_revision: 2 },
    }
    vi.spyOn(evaluationService, 'listV2Candidates').mockResolvedValue([candidate, revisedCandidate])
    const container = await mount()
    const checkboxes = container.querySelectorAll('input[type="checkbox"]')
    expect(checkboxes).toHaveLength(1)
    await act(async () => { (checkboxes[0] as HTMLInputElement).click(); await Promise.resolve() })
    const publish = Array.from(container.querySelectorAll('button')).find(button => button.textContent === '发布不可变 Golden 版本')
    await act(async () => { publish?.click(); await Promise.resolve(); await Promise.resolve() })

    expect(evaluationService.publishV2DatasetVersion).toHaveBeenCalledWith('dataset-1', {
      version: '1.0.0', items: [{ case_id: 'case-1', revision: 2 }],
    })
  })

  it('从已审核 Case 创建新修订候选并转到原审核队列', async () => {
    vi.spyOn(evaluationService, 'getV2CaseLatestRevision').mockResolvedValue({
      case_id: 'case-1', lifecycle_status: 'published', revision: 1,
      input_json: { query: '如何申请年假？' }, expected_json: { behavior: 'refuse', reason: 'no_evidence' },
      evidence_json: [], metadata_json: {}, source_ref: { dataset_key: 'support_golden' }, content_hash: 'hash',
    })
    const create = vi.spyOn(evaluationService, 'createV2CaseRevisionCandidate').mockResolvedValue(candidate)
    const onGoReview = vi.fn()
    const container = await mount(onGoReview)
    const summary = Array.from(container.querySelectorAll('summary')).find(item => item.textContent === '修订已审核 Case')
    await act(async () => { summary?.click(); await Promise.resolve() })
    const target = container.querySelector('select[aria-label="目标 Case"]') as HTMLSelectElement
    const setSelectValue = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set
    await act(async () => {
      setSelectValue?.call(target, 'case-1')
      target.dispatchEvent(new Event('change', { bubbles: true }))
      await new Promise(resolve => setTimeout(resolve, 0))
    })
    const query = container.querySelector('textarea[aria-label="修订评测问题"]') as HTMLTextAreaElement
    const setTextValue = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set
    await act(async () => {
      setTextValue?.call(query, '新版问题')
      query.dispatchEvent(new Event('input', { bubbles: true }))
      query.dispatchEvent(new Event('change', { bubbles: true }))
    })
    const submit = Array.from(container.querySelectorAll('button')).find(button => button.textContent === '提交修订候选并转审核')
    await act(async () => { submit?.click(); await new Promise(resolve => setTimeout(resolve, 0)); await new Promise(resolve => setTimeout(resolve, 0)) })

    expect(create).toHaveBeenCalledWith('case-1', expect.objectContaining({ query: '新版问题', observed_answer: '' }))
    expect(create.mock.calls[0][1].idempotency_key).toMatch(/^[A-Za-z0-9_-]{8,128}$/)
    expect(onGoReview).toHaveBeenCalledOnce()
  })

  it('从已发布版本行下载服务端校验后的 JSONL', async () => {
    vi.spyOn(evaluationService, 'listV2DatasetVersions').mockResolvedValue([publishedVersion])
    vi.spyOn(evaluationService, 'exportV2DatasetVersion').mockResolvedValue(new Blob(['{"type":"dataset_manifest"}']))
    vi.spyOn(evaluationService, 'getV2DatasetCoverage').mockResolvedValue({
      dataset_id: 'dataset-1', dataset_version_id: 'version-1', version: '1.0.0', content_hash: 'hash', case_count: 1,
      dimensions: {
        category: { covered_count: 1, coverage_ratio: 100, items: [{ label: 'policy', count: 1, ratio: 100 }], other_count: 0 },
        scenario: { covered_count: 0, coverage_ratio: 0, items: [], other_count: 0 },
        risk_level: { covered_count: 0, coverage_ratio: 0, items: [], other_count: 0 },
        difficulty: { covered_count: 0, coverage_ratio: 0, items: [], other_count: 0 },
      },
    })
    const createUrl = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:evaluation')
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    const container = await mount()
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })

    const button = container.querySelector('button[aria-label="导出 Golden 1.0.0 JSONL"]') as HTMLButtonElement
    expect(button).not.toBeNull()
    await act(async () => { button.click(); await new Promise(resolve => setTimeout(resolve, 0)) })

    expect(evaluationService.exportV2DatasetVersion).toHaveBeenCalledWith('dataset-1', 'version-1')
    expect(createUrl).toHaveBeenCalledWith(expect.any(Blob))
    expect(click).toHaveBeenCalledOnce()
  })

  it('按目标 Golden 版本展示脱敏后的覆盖分布', async () => {
    const loadCoverage = vi.spyOn(evaluationService, 'getV2DatasetCoverage').mockResolvedValue({
      dataset_id: 'dataset-1', dataset_version_id: 'version-1', version: '1.0.0', content_hash: 'hash', case_count: 1,
      dimensions: {
        category: { covered_count: 1, coverage_ratio: 100, items: [{ label: 'policy', count: 1, ratio: 100 }], other_count: 0 },
        scenario: { covered_count: 0, coverage_ratio: 0, items: [{ label: '未标注', count: 1, ratio: 100 }], other_count: 0 },
        risk_level: { covered_count: 0, coverage_ratio: 0, items: [{ label: '未标注', count: 1, ratio: 100 }], other_count: 0 },
        difficulty: { covered_count: 0, coverage_ratio: 0, items: [{ label: '未标注', count: 1, ratio: 100 }], other_count: 0 },
      },
    })
    vi.spyOn(evaluationService, 'listV2DatasetVersions').mockResolvedValue([publishedVersion])
    const container = await mount()
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); await new Promise(resolve => setTimeout(resolve, 0)) })

    expect(loadCoverage).toHaveBeenCalledWith('dataset-1', 'version-1')
    expect(container.textContent).toContain('版本覆盖分布 · v1.0.0')
    expect(container.textContent).toContain('类别')
    expect(container.textContent).toContain('policy')
    expect(container.textContent).toContain('覆盖 100% · 1/1')
  })
})
