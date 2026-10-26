import { afterEach, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import ConfigHistoryTab from './ConfigHistoryTab'

const restore = vi.hoisted(() => vi.fn().mockResolvedValue({}))
vi.mock('@/api/modelConfig', () => ({ rollbackConfigHistory: restore }))
vi.mock('@/components/shared/Toast', () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }))

const mounted: (() => void)[] = []
afterEach(() => { mounted.splice(0).forEach(fn => fn()); restore.mockClear() })

it('用户名与原始审计身份均可见，恢复前必须确认', async () => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  mounted.push(() => { act(() => root.unmount()); container.remove() })
  act(() => root.render(<ConfigHistoryTab canAdmin onChanged={vi.fn(async () => undefined)} items={[{
    id: '1', object: 'role', key: 'main', oldValue: 'old-model', newValue: 'new-model',
    operator: 'user:7', operatorName: '张三', at: '2026-10-26T01:00:00Z', rollbackable: true,
  }]} />))
  expect(container.textContent).toContain('张三')
  expect(container.textContent).toContain('主问答模型')
  expect(container.querySelector('details')?.textContent).toContain('user:7')
  await act(async () => { container.querySelector('button')?.click() })
  expect(restore).not.toHaveBeenCalled()
  expect(container.querySelector('[role="dialog"]')?.textContent).toContain('old-model')
  await act(async () => {
    Array.from(container.querySelectorAll('button')).find(b => b.textContent === '确认恢复')?.click()
  })
  expect(restore).toHaveBeenCalledWith('1')
})
