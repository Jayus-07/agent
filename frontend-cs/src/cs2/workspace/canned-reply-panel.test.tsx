// cs-workstation-v2 P4：快捷回复面板（加载/搜索/分类过滤/插入/禁用置灰/空态）。
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

const api = vi.hoisted(() => ({
  listCanned: vi.fn(async () => [
    { id: 1, scope: 'personal', owner_agent_id: 'a1', title: '开场问候', content: '您好，很高兴为您服务', category: '通用', enabled: true, updated_at: null },
    { id: 2, scope: 'team', owner_agent_id: null, title: '退款时效', content: '退款 1-3 个工作日到账', category: '售后', enabled: true, updated_at: null },
    { id: 3, scope: 'team', owner_agent_id: null, title: '物流安抚', content: '物流正在加急派送', category: '售后', enabled: false, updated_at: null },
  ]),
}))

vi.mock('./api', () => api)

import CannedReplyPanel from './CannedReplyPanel'

beforeAll(() => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

let mounted: { container: HTMLElement; root: Root } | null = null

afterEach(() => {
  if (mounted) {
    act(() => mounted?.root.unmount())
    mounted.container.remove()
    mounted = null
  }
  vi.clearAllMocks()
})

async function mount(open = true) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  await act(async () => {
    root.render(<CannedReplyPanel open={open} onClose={vi.fn()} onInsert={vi.fn()} />)
  })
  mounted = { container, root }
  return container
}

describe('CannedReplyPanel（P4 快捷回复）', () => {
  it('open=false 不渲染', async () => {
    const c = await mount(false)
    expect(c.textContent).toBe('')
    expect(api.listCanned).not.toHaveBeenCalled()
  })

  it('打开加载列表并渲染个人/团队徽章', async () => {
    const c = await mount()
    await act(async () => {})
    expect(api.listCanned).toHaveBeenCalledTimes(1)
    expect(c.textContent).toContain('开场问候')
    expect(c.textContent).toContain('团队')
    expect(c.textContent).toContain('个人')
  })

  it('搜索按标题/内容过滤，空态提示', async () => {
    const c = await mount()
    await act(async () => {})
    const search = c.querySelector('input[aria-label="搜索话术"]') as HTMLInputElement
    const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setValue.call(search, '加急')
      search.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(c.textContent).toContain('物流安抚')
    expect(c.textContent).not.toContain('开场问候')
    await act(async () => {
      setValue.call(search, '不存在的话术')
      search.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(c.textContent).toContain('无匹配话术')
  })

  it('分类 chips 过滤', async () => {
    const c = await mount()
    await act(async () => {})
    const cat = Array.from(c.querySelectorAll('button')).find((b) => b.textContent === '售后')
    await act(async () => {
      cat?.click()
    })
    expect(c.textContent).toContain('退款时效')
    expect(c.textContent).not.toContain('开场问候')
  })

  it('禁用话术置灰不可点；点击启用项回调插入', async () => {
    const onInsert = vi.fn()
    const onClose = vi.fn()
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    await act(async () => {
      root.render(<CannedReplyPanel open onClose={onClose} onInsert={onInsert} />)
    })
    mounted = { container, root }
    await act(async () => {})

    const disabled = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent?.includes('物流安抚'),
    ) as HTMLButtonElement
    expect(disabled.disabled).toBe(true)
    await act(async () => {
      disabled.click()
    })
    expect(onInsert).not.toHaveBeenCalled()

    const item = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent?.includes('开场问候'),
    )!
    await act(async () => {
      item.click()
    })
    expect(onInsert).toHaveBeenCalledWith('您好，很高兴为您服务')
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
