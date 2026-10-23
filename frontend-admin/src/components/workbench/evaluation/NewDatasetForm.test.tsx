import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { evaluationAssets } from '@/api/evaluation-assets'
vi.mock('@/api/evaluation-assets', () => ({ evaluationAssets: { validate: vi.fn(async () => ({ valid: true })), import: vi.fn(async () => ({})) } }))
import NewDatasetForm from './NewDatasetForm'
it('普通表单可创建拒答题集并提交审核，缺少问题时阻止保存', async () => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  const container = document.createElement('div'); document.body.appendChild(container)
  const root = createRoot(container); const saved = vi.fn(async () => {})
  const change = async (selector: string, value: string, kind = 'input') => {
    const node = container.querySelector(selector) as HTMLInputElement
    await act(async () => { Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), 'value')!.set!.call(node, value); node.dispatchEvent(new Event(kind, { bubbles: true })) })
  }
  try {
    await act(async () => root.render(<NewDatasetForm onSaved={saved} />))
    await change('[aria-label="新评测集名称"]', '制度题集')
    const submit = () => Array.from(container.querySelectorAll('button')).find(b => b.textContent === '保存并提交审核')!
    await act(async () => submit().click())
    expect(container.textContent).toContain('请填写问题')
    expect(evaluationAssets.import).not.toHaveBeenCalled()
    await change('[aria-label="题目问题"]', '未知制度应拒答吗？')
    await change('[aria-label="预期行为"]', 'refuse', 'change')
    await act(async () => submit().click())
    expect(evaluationAssets.validate).toHaveBeenCalledOnce()
    expect(evaluationAssets.import).toHaveBeenCalledOnce()
    expect(saved).toHaveBeenCalledOnce()
    expect(container.textContent).toContain('评测集已保存并提交审核')
    await act(async () => Array.from(container.querySelectorAll('button')).find(b => b.textContent === '继续新建评测集')!.click())
    expect((container.querySelector('[aria-label="新评测集名称"]') as HTMLInputElement).value).toBe('')
    expect((container.querySelector('[aria-label="题目问题"]') as HTMLTextAreaElement).value).toBe('')
  } finally { act(() => root.unmount()); container.remove() }
})
