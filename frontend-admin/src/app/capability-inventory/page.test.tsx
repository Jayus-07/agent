import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getCsInventory = vi.hoisted(() => vi.fn())

vi.mock('@/api/csInventory', () => ({ getCsInventory }))
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/capability-inventory',
  useSearchParams: () => new URLSearchParams(),
}))

import CsInventoryPage from './page'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

const mounted: { container: HTMLDivElement; root: Root }[] = []

beforeEach(() => {
  getCsInventory.mockResolvedValue({
    domain: 'customer_service',
    range: 'd7',
    total: 2,
    intents: [
      {
        intent: 'k_policy', description: '政策咨询',
        stats: { calls: 3, handoff_rate: 1.0, unresolved: 1 },
      },
      {
        intent: 'as_refund', description: '退款',
        stats: { calls: 1, handoff_rate: 0.0, unresolved: 0 },
      },
    ],
    totals: { calls: 4, intents_with_data: 2, unresolved: 1 },
  })
})

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
  vi.clearAllMocks()
})

async function renderPage() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  mounted.push({ container, root })

  await act(async () => {
    root.render(<CsInventoryPage />)
    await new Promise((resolve) => setTimeout(resolve, 0))
  })

  return container
}

describe('客服意图目录页（G4 治理）', () => {
  it('渲染汇总卡与意图三元组表格（含转人工率与未解决标注）', async () => {
    await renderPage()
    await act(async () => { await new Promise((r) => setTimeout(r, 0)) })

    expect(document.body.textContent).toContain('意图总数')
    expect(document.body.textContent).toContain('未解决')
    const table = document.querySelector('[data-testid="cs-inventory-table"]')
    expect(table).not.toBeNull()
    expect(document.body.textContent).toContain('k_policy')
    expect(document.body.textContent).toContain('政策咨询')
    expect(document.body.textContent).toContain('100.0%')
    expect(getCsInventory).toHaveBeenCalledWith('d7')
  })

  it('切换时间窗后按新窗口拉取', async () => {
    await renderPage()
    await act(async () => { await new Promise((r) => setTimeout(r, 0)) })

    const select = document.querySelector(
      '[data-testid="cs-inventory-range"]') as HTMLSelectElement
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLSelectElement.prototype, 'value')?.set
      setter?.call(select, 'd30')
      select.dispatchEvent(new Event('change', { bubbles: true }))
      await new Promise((r) => setTimeout(r, 0))
    })

    expect(getCsInventory).toHaveBeenCalledWith('d30')
  })
})
