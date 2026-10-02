import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { evaluationService } from '@/api/evaluation'

const toast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn(), show: vi.fn() }))

vi.mock('@/components/shared/Toast', () => ({
  useToast: () => toast,
}))

vi.mock('@/components/evaluations/DatasetCatalog', () => ({
  default: () => <div data-child="catalog">数据集目录</div>,
}))
vi.mock('@/components/evaluations/DatasetReviewQueue', () => ({
  default: () => <div data-child="review">审核队列</div>,
}))
vi.mock('@/components/evaluations/DatasetVersionDetail', () => ({
  default: () => <div data-child="version">版本详情</div>,
}))
vi.mock('@/components/evaluations/EvaluationRunDetail', () => ({
  default: () => <div data-child="run">评测运行详情</div>,
}))

import DatasetGovernancePanel from './DatasetGovernancePanel'

let mounted: { root: Root; container: HTMLDivElement } | null = null

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

beforeEach(() => {
  vi.spyOn(evaluationService, 'listDatasets').mockResolvedValue([{
    dataset_id: 'rag-golden',
    module: 'rag',
    dataset_version: 'v1',
    owner: 'qa',
    review_status: 'published',
    case_count: 1,
    content_hash: 'hash',
    coverage: {},
    kb_id: 'general',
    fixture_set: 'golden',
  }])
  vi.spyOn(evaluationService, 'listDatasetCandidates').mockResolvedValue([])
  vi.spyOn(evaluationService, 'listSuites').mockResolvedValue([])
  vi.spyOn(evaluationService, 'listRuns').mockResolvedValue([{ run_id: 'run-1' }] as never)
  vi.spyOn(evaluationService, 'getDatasetVersion').mockResolvedValue({} as never)
  vi.spyOn(evaluationService, 'getRun').mockResolvedValue({} as never)
})

afterEach(() => {
  mounted?.root.unmount()
  mounted?.container.remove()
  mounted = null
  vi.restoreAllMocks()
})

describe('DatasetGovernancePanel', () => {
  it('加载评测集治理数据并组合四类治理子组件', async () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted = { root, container }

    act(() => root.render(<DatasetGovernancePanel />))
    await act(async () => {
      await Promise.resolve()
    })

    expect(evaluationService.listDatasets).toHaveBeenCalledTimes(1)
    expect(container.querySelectorAll('[data-child]')).toHaveLength(4)
  })
})
