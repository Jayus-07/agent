import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { evaluationAssets, type ImportedDataset } from '@/api/evaluation-assets'

vi.mock('@/api/evaluation-assets', () => ({ evaluationAssets: {
  detail: vi.fn(async () => ({ cases: [{ id: 'P-1', question: '请假流程是什么？', expected: { behavior: 'answer', evidence: [{ doc_id: 'doc-1', quote: '提前三天提交申请' }], reference_answer: '提前三天申请', required_facts: [] } }] })),
  revise: vi.fn(async () => ({})), label: vi.fn(), diff: vi.fn(),
} }))
import DatasetRevisionEditor from './DatasetRevisionEditor'

let root: Root
let container: HTMLDivElement
beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })
afterEach(() => { act(() => root?.unmount()); document.body.innerHTML = ''; vi.clearAllMocks() })
const button = (label: string) => Array.from(container.querySelectorAll('button')).find(item => item.textContent === label)!

describe('表单修订评测题目', () => {
  it('无需编辑 JSON 即可修改问题、归档并保存待审核版本', async () => {
    container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container)
    const done = vi.fn(async () => {})
    await act(async () => root.render(<DatasetRevisionEditor item={{ import_id: 'old', manifest: { name: '制度', version: '1.0.0' }, review: { decision: 'approved' } } as ImportedDataset} done={done} />))
    await act(async () => button('编辑题目').click())
    const question = container.querySelector('textarea[aria-label="题目问题"]')!
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(question, '修改后的请假流程？'); question.dispatchEvent(new Event('input', { bubbles: true })) })
    await act(async () => button('保存新版本并提交审核').click())
    expect(evaluationAssets.revise).toHaveBeenCalledWith('old', '1.0.1', expect.objectContaining({ 'P-1': expect.objectContaining({ question: '修改后的请假流程？' }) }))
    expect(done).toHaveBeenCalledOnce()
    expect(container.textContent).toContain('新版本已保存')
  })
})
