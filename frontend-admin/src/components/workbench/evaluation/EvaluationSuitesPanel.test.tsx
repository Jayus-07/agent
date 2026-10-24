import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { evaluationService } from '@/api/evaluation'
import EvaluationSuitesPanel from './EvaluationSuitesPanel'

let mounted: { root: Root; container: HTMLDivElement } | null = null
beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })
beforeEach(() => {
  vi.spyOn(evaluationService, 'listSuites').mockResolvedValue([])
  vi.spyOn(evaluationService, 'listV2Datasets').mockResolvedValue([
    { dataset_id: 'dataset-travel', dataset_key: 'travel_golden', name: '旅游集', domain: 'travel', description: '', status: 'active' },
    { dataset_id: 'dataset-rag', dataset_key: 'rag_golden', name: 'RAG 集', domain: 'rag', description: '', status: 'active' },
  ])
  vi.spyOn(evaluationService, 'listV2DatasetVersions').mockImplementation(async datasetId => [
    { dataset_version_id: `${datasetId}-version`, dataset_id: datasetId, version: '1.0.0', status: 'published', content_hash: 'dataset-hash', case_count: 4 },
  ])
  vi.spyOn(evaluationService, 'listV2Evaluators').mockResolvedValue([
    { evaluator_version_id: 'evaluator-version-1', evaluator_key: 'answer_quality', version: '1.0.0', evaluator_kind: 'deterministic', config_json: {}, rubric_hash: 'rubric-hash', status: 'published' },
    { evaluator_version_id: 'evidence-evaluator-1', evaluator_key: 'evidence_quality', version: '1.0.0', evaluator_kind: 'evidence', config_json: {}, rubric_hash: 'evidence-rubric-hash', status: 'published' },
    { evaluator_version_id: 'human-evaluator-1', evaluator_key: 'human_quality', version: '1.0.0', evaluator_kind: 'human', config_json: { criteria: [] }, rubric_hash: 'human-rubric-hash', status: 'published' },
  ])
  vi.spyOn(evaluationService, 'createV2Suite').mockResolvedValue({ suite_id: 'suite-1', suite_version_id: 'suite-version-1', version: '1.0.0', status: 'draft', snapshot_hash: 'snapshot-hash' })
  vi.spyOn(evaluationService, 'publishV2Suite').mockResolvedValue({ suite_version_id: 'suite-version-1', status: 'published', snapshot_hash: 'snapshot-hash' })
})
afterEach(() => {
  mounted?.root.unmount()
  mounted?.container.remove()
  mounted = null
  vi.restoreAllMocks()
})

describe('EvaluationSuitesPanel', () => {
  it('仅以已发布数据和评分器版本创建草稿，再通过独立操作发布', async () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted = { root, container }
    await act(async () => { root.render(<EvaluationSuitesPanel onNavigate={() => undefined} />); await new Promise(resolve => setTimeout(resolve, 0)) })

    const create = Array.from(container.querySelectorAll('button')).find(button => button.textContent === '保存方案草稿')
    expect(create?.disabled).toBe(false)
    const humanRubric = container.querySelector('select[aria-label="人工复核 Rubric"]') as HTMLSelectElement
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(humanRubric, 'human-evaluator-1')
      humanRubric.dispatchEvent(new Event('change', { bubbles: true }))
    })
    await act(async () => { create?.click(); await new Promise(resolve => setTimeout(resolve, 0)) })
    expect(evaluationService.createV2Suite).toHaveBeenCalledWith(expect.objectContaining({
      module: 'rag',
      dataset_version_id: 'dataset-rag-version',
      evaluator_versions: [
        { evaluator_version_id: 'evaluator-version-1', binding: 'answer_quality' },
        { evaluator_version_id: 'human-evaluator-1', binding: 'human_review' },
      ],
      minimum_valid_samples: 1,
    }))

    const publish = Array.from(container.querySelectorAll('button')).find(button => button.textContent === '发布此方案版本')
    expect(publish).not.toBeNull()
    await act(async () => { publish?.click(); await new Promise(resolve => setTimeout(resolve, 0)) })
    expect(evaluationService.publishV2Suite).toHaveBeenCalledWith('suite-version-1')
  })

  it('只开放有已发布 Golden 的受控模块，并说明契约检查边界', async () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted = { root, container }
    await act(async () => { root.render(<EvaluationSuitesPanel onNavigate={() => undefined} />); await new Promise(resolve => setTimeout(resolve, 0)) })

    const moduleSelect = container.querySelector('select[aria-label="执行模块"]') as HTMLSelectElement
    expect(Array.from(moduleSelect.options).map(option => option.value)).toEqual(['rag', 'travel'])
    expect(container.textContent).toContain('客服和 E2E 当前只运行 Golden 契约检查，Gate 固定为 INVALID')
  })

  it('允许只绑定证据评分器创建检索方案，不隐式启用答案生成', async () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted = { root, container }
    await act(async () => { root.render(<EvaluationSuitesPanel onNavigate={() => undefined} />); await new Promise(resolve => setTimeout(resolve, 0)) })

    const answerEvaluator = container.querySelector('select[aria-label="确定性答案评分器（可选）"]') as HTMLSelectElement
    const evidenceEvaluator = container.querySelector('select[aria-label="证据评分器"]') as HTMLSelectElement
    const create = Array.from(container.querySelectorAll('button')).find(button => button.textContent === '保存方案草稿')
    expect(create?.disabled).toBe(false)

    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(answerEvaluator, '')
      answerEvaluator.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(create?.disabled).toBe(true)

    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(evidenceEvaluator, 'evidence-evaluator-1')
      evidenceEvaluator.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(create?.disabled).toBe(false)
    await act(async () => { create?.click(); await new Promise(resolve => setTimeout(resolve, 0)) })
    expect(evaluationService.createV2Suite).toHaveBeenCalledWith(expect.objectContaining({
      evaluator_versions: [{ evaluator_version_id: 'evidence-evaluator-1', binding: 'evidence_quality' }],
    }))
  })

  it('非 RAG 模块绑定本域已发布 Golden 和确定性结果评分器', async () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted = { root, container }
    await act(async () => { root.render(<EvaluationSuitesPanel onNavigate={() => undefined} />); await new Promise(resolve => setTimeout(resolve, 0)) })

    const moduleSelect = container.querySelector('select[aria-label="执行模块"]') as HTMLSelectElement
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(moduleSelect, 'travel')
      moduleSelect.dispatchEvent(new Event('change', { bubbles: true }))
    })
    const create = Array.from(container.querySelectorAll('button')).find(button => button.textContent === '保存方案草稿')
    await act(async () => { create?.click(); await new Promise(resolve => setTimeout(resolve, 0)) })

    expect(evaluationService.createV2Suite).toHaveBeenCalledWith(expect.objectContaining({
      module: 'travel',
      dataset_version_id: 'dataset-travel-version',
      evaluator_versions: [{ evaluator_version_id: 'evaluator-version-1', binding: 'answer_quality' }],
    }))
  })
})
