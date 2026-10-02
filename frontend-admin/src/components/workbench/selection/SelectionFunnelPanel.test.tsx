import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { selectionFunnelService } from '@/api/selectionFunnel'
import SelectionFunnelPanel from './SelectionFunnelPanel'

let mounted: { root: Root; container: HTMLDivElement } | null = null

beforeEach(() => {
  vi.spyOn(selectionFunnelService, 'listCandidates').mockResolvedValue({
    count: 1,
    items: [{
      id: 1,
      batch_id: 'imp-1',
      title: '降噪耳机',
      platform: '淘宝',
      price: 129,
      original_price: null,
      rating: 4.8,
      review_count: 100,
      sales: 20,
      category: '蓝牙耳机',
      url: 'https://example.com/item/1',
      promo_text: '',
      highlights: '',
      history_batches: 2,
    }],
  })
  vi.spyOn(selectionFunnelService, 'marketSnapshot').mockResolvedValue({
    count: 1,
    top: [{
      keyword: '降噪耳机',
      search_pop: 1000,
      click_rate: null,
      pay_rate: null,
      competition: 20,
    }],
    opportunities: [],
  })
})

afterEach(() => {
  mounted?.root.unmount()
  mounted?.container.remove()
  mounted = null
  vi.restoreAllMocks()
})

function mount(): HTMLDivElement {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  mounted = { root, container }
  act(() => root.render(<SelectionFunnelPanel />))
  return container
}

describe('SelectionFunnelPanel', () => {
  it('加载并展示候选池和赛道画像，不渲染独立页面标题壳', async () => {
    const container = mount()

    await act(async () => {
      await Promise.resolve()
    })

    expect(container.textContent).toContain('降噪耳机')
    expect(container.textContent).toContain('赛道画像')
    expect(container.textContent).toContain('候选池')
    expect(container.textContent).not.toContain('选品漏斗 · 数据工作台')
    expect(container.querySelector('.overflow-y-auto')).toBeNull()
  })
})
