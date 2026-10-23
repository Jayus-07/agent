import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { evaluationAssets } from '@/api/evaluation-assets'

const toast = vi.hoisted(() => ({ success: vi.fn(), warning: vi.fn() }))
vi.mock('@/components/shared/Toast', () => ({ useToast: () => toast }))
vi.mock('@/lib/auth', () => ({ atLeast: () => true }))
vi.mock('next/navigation', () => ({ useSearchParams: () => new URLSearchParams('kb_id=policy_general&import_id=published') }))
vi.mock('@/api/knowledge', () => ({ listKbAuthority: vi.fn(async () => ({ knowledge_bases: [{ kb_id: 'policy_general', name: '公共制度', active: true }, { kb_id: 'legacy', name: '兼容库', active: true, alias_for: 'policy_general' }] })) }))
vi.mock('@/api/evaluation-assets', () => ({ evaluationAssets: {
  list: vi.fn(async () => [{ import_id: 'published', manifest: { name: '题集', version: '1.0.0' }, review: { decision: 'approved' } }, { import_id: 'draft', manifest: { name: '草稿' }, review: { decision: 'pending_review' } }]),
  bindings: vi.fn(async () => [{ binding_id: 'binding', import_id: 'published', kb_id: 'policy_general', dataset_name: '题集', dataset_version: '1.0.0' }]),
  bind: vi.fn(), freeze: vi.fn(async () => ({ snapshot_id: 'snapshot' })),
  run: vi.fn(async () => ({ run_id: 'run-1', task_id: 'task-1', status: 'PENDING' })),
  task: vi.fn(async () => ({ task_id: 'task-1', status: 'SUCCESS', output: { run_id: 'run-1', validity: 'INVALID_DATASET', total: 1 } })),
} }))
vi.mock('@/api/evaluation', () => ({ evaluationService: { getRun: vi.fn(async () => ({ run_id: 'run-1', report: { results: [], metadata: { run_validity: { validity: 'INVALID_DATASET' } } } })) } }))
vi.mock('@/components/evaluations/CaseSampleDetail', () => ({ default: () => null }))
import EvaluationBindingsPanel from './EvaluationBindingsPanel'

let root: Root
let container: HTMLDivElement
beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })
afterEach(() => { act(() => root?.unmount()); document.body.innerHTML = ''; localStorage.clear(); vi.useRealTimers(); vi.clearAllMocks() })
async function mount() {
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container)
  await act(async () => root.render(<EvaluationBindingsPanel />))
}
const button = (label: string) => Array.from(container.querySelectorAll('button')).find(item => item.textContent === label)!

describe('知识库评测关联', () => {
  it('深链接预选目标，仅允许已发布题集与正式知识库；失败保持可见', async () => {
    await mount()
    expect(Array.from(container.querySelectorAll('option')).some(item => item.value === 'draft' || item.value === 'legacy')).toBe(false)
    vi.mocked(evaluationAssets.bind).mockRejectedValueOnce(new Error('证据版本不一致'))
    await act(async () => button('校验证据并关联').click())
    expect(evaluationAssets.bind).toHaveBeenCalledWith('published', 'policy_general', expect.objectContaining({ snapshot_id: 'snapshot', mode: 'retrieval' }))
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('证据版本不一致')
    expect(toast.success).not.toHaveBeenCalled()
  })
  it('运行无效必须展示原因，不能报告质量通过', async () => {
    vi.useFakeTimers()
    await mount()
    await act(async () => button('运行评测').click())
    expect(evaluationAssets.run).toHaveBeenCalledWith('binding', expect.any(String))
    expect(container.textContent).toContain('排队中')
    await act(async () => { await vi.advanceTimersByTimeAsync(2100) })
    expect(toast.warning).toHaveBeenCalledOnce()
    expect(toast.success).not.toHaveBeenCalled()
    expect(container.textContent).toContain('INVALID_DATASET')
  })
})
