import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { evaluationAssets } from '@/api/evaluation-assets'

vi.mock('@/lib/auth', () => ({ atLeast: () => true }))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('@/components/shared/Toast', () => ({ useToast: () => toast }))
vi.mock('@/api/evaluation-assets', () => ({
  downloadEvalTemplate: vi.fn(),
  evaluationAssets: { list: vi.fn(async () => []), validate: vi.fn(), import: vi.fn(), review: vi.fn(), detail: vi.fn() },
}))
import ImportedDatasetsPanel from './ImportedDatasetsPanel'

let root: Root
let container: HTMLDivElement
beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })
afterEach(() => { act(() => root?.unmount()); document.body.innerHTML = ''; vi.clearAllMocks() })
async function mount(review = false) {
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container)
  await act(async () => { root.render(<ImportedDatasetsPanel review={review} />) })
}
const button = (label: string) => Array.from(container.querySelectorAll('button')).find(item => item.textContent === label)!

describe('导入题集操作链', () => {
  it('校验失败显示字段与行号，禁止提交；校验通过后才允许提交', async () => {
    vi.mocked(evaluationAssets.validate).mockResolvedValueOnce({ valid: false, errors: [{ line: 2, field: 'expected', message: '缺少证据' }] })
    await mount()
    await act(async () => button('批量导入').click())
    const input = container.querySelector('input[type="file"]')!
    Object.defineProperty(input, 'files', { configurable: true, value: [new File(['{}'], 'test.json')] })
    await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })))
    await act(async () => button('校验并预览').click())
    expect(container.textContent).toContain('第 2 行 · expected：缺少证据')
    expect(button('提交审核').disabled).toBe(true)
    expect(evaluationAssets.import).not.toHaveBeenCalled()
    vi.mocked(evaluationAssets.validate).mockResolvedValueOnce({ valid: true, errors: [], case_count: 1 })
    await act(async () => button('校验并预览').click())
    expect(button('提交审核').disabled).toBe(false)
    vi.mocked(evaluationAssets.import).mockResolvedValueOnce({ import_id: 'new' } as never)
    await act(async () => button('提交审核').click())
    expect(evaluationAssets.import).toHaveBeenCalledOnce()
    expect(button('提交审核').disabled).toBe(true)
  })

  it('审核必须先读取全部题目且填写审核说明，才能发布', async () => {
    vi.mocked(evaluationAssets.list).mockResolvedValueOnce([{
      import_id: 'x', manifest: { name: '黄金题集', version: '1.0.0', dataset_key: 'golden' }, case_count: 4,
      review: { decision: 'pending_review' }, preview: [], content_hash: 'hash',
    }] as never)
    vi.mocked(evaluationAssets.detail).mockResolvedValueOnce({ cases: [{ id: 'all-four' }] } as never)
    await mount(true)
    expect(button('核验并发布').disabled).toBe(true)
    const reason = container.querySelector('input')!
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(reason, '已逐条核验原文'); reason.dispatchEvent(new Event('input', { bubbles: true })) })
    expect(button('核验并发布').disabled).toBe(true)
    await act(async () => button('读取全部 4 题').click())
    expect(container.textContent).toContain('all-four')
    expect(button('核验并发布').disabled).toBe(false)
  })
})
