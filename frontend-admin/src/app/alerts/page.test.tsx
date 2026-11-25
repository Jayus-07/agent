import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import AlertsPage from './page'
import { alertService } from '@/api/alerts'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
}))

const mockStats = {
  stats: {
    critical: 2,
    warning: 5,
    info: 1,
    resolved: 10,
  },
}

const mockAlerts = {
  cases: [
    {
      id: 101,
      product_id: 'SKU-8899',
      current_state: 'low_stock',
      current_level: 'critical',
      status: 'open',
      resolution_type: null,
      first_detected_at: '2026-11-25T08:00:00Z',
    },
  ],
  total: 1,
  page: 1,
  page_size: 20,
}

const mockDetail = {
  case: {
    id: 101,
    product_id: 'SKU-8899',
    current_state: 'low_stock',
    current_level: 'critical',
    status: 'open',
    resolution_type: null,
    first_detected_at: '2026-11-25T08:00:00Z',
  },
  events: [
    {
      id: 1,
      case_id: 101,
      event_type: 'created',
      from_state: null,
      to_state: 'low_stock',
      reason: ['低于安全库存 5 件'],
      notified: true,
      created_at: '2026-11-25T08:00:00Z',
    },
  ],
}

const mounted: { container: HTMLDivElement; root: Root }[] = []

afterEach(() => {
  vi.restoreAllMocks()
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
})

describe('AlertsPage — 商品库存预警页面重构', () => {
  it('渲染标准页面外壳、指标条与工单列表', async () => {
    vi.spyOn(alertService, 'getStats').mockResolvedValue(mockStats)
    vi.spyOn(alertService, 'getAlerts').mockResolvedValue(mockAlerts)

    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted.push({ container, root })

    await act(async () => {
      root.render(<AlertsPage />)
    })

    // 页面标题与指标
    expect(container.textContent).toContain('商品库存预警')
    expect(container.textContent).toContain('选品与商城业务层')
    expect(container.textContent).toContain('紧急告警')
    expect(container.textContent).toContain('2')
    expect(container.textContent).toContain('警告事件')
    expect(container.textContent).toContain('5')

    // 列表项渲染
    expect(container.textContent).toContain('SKU-8899')
    expect(container.textContent).toContain('low_stock')
    expect(container.textContent).toContain('待处理')
  })

  it('点击工单卡片弹出 DetailDrawer 抽屉并展示处置面板', async () => {
    vi.spyOn(alertService, 'getStats').mockResolvedValue(mockStats)
    vi.spyOn(alertService, 'getAlerts').mockResolvedValue(mockAlerts)
    vi.spyOn(alertService, 'getAlert').mockResolvedValue(mockDetail)

    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted.push({ container, root })

    await act(async () => {
      root.render(<AlertsPage />)
    })

    // 找到工单按钮并点击
    const itemButton = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('SKU-8899'),
    )
    expect(itemButton).toBeTruthy()

    await act(async () => {
      itemButton?.click()
    })

    // 检查抽屉是否出现
    expect(document.body.textContent).toContain('SKU-8899 · 库存预警工单')
    expect(document.body.textContent).toContain('工单处置')
    expect(document.body.textContent).toContain('确认预警')
    expect(document.body.textContent).toContain('标记已解决')
    expect(document.body.textContent).toContain('事件时间线')
    expect(document.body.textContent).toContain('低于安全库存 5 件')
  })
})
