import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import ReportsPage from './page'
import { reportService } from '@/api/reports'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
}))

const mockLatest = {
  report: {
    id: 'rep-001',
    report_date: '2026-11-25',
    report_type: 'daily_report',
    status: 'success',
    kpi_summary: {
      total_products: 120,
      alert_count: 3,
      sales_records: 450,
      report_date: '2026-11-25',
    },
    trace_id: 'trace-daily-99',
    created_at: '2026-11-25T07:00:00Z',
    report_content: '### 今日经营核心指标\n\n- 商品销售平稳\n- 预警 3 款库存告急',
  },
}

const mockList = {
  reports: [
    {
      id: 'rep-001',
      report_date: '2026-11-25',
      report_type: 'daily_report',
      status: 'success',
      kpi_summary: {
        total_products: 120,
        alert_count: 3,
        sales_records: 450,
        report_date: '2026-11-25',
      },
      trace_id: 'trace-daily-99',
      created_at: '2026-11-25T07:00:00Z',
    },
  ],
  page: 1,
  page_size: 20,
}

const mounted: { container: HTMLDivElement; root: Root }[] = []

afterEach(() => {
  vi.restoreAllMocks()
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
})

describe('ReportsPage — 报告中心页面重构', () => {
  it('渲染标准页面外壳、指标条与日报归档列表', async () => {
    vi.spyOn(reportService, 'getLatestReport').mockResolvedValue(mockLatest)
    vi.spyOn(reportService, 'getReports').mockResolvedValue(mockList)

    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted.push({ container, root })

    await act(async () => {
      root.render(<ReportsPage />)
    })

    // 页面标题与指标
    expect(container.textContent).toContain('报告中心')
    expect(container.textContent).toContain('Workflow 自动生成')
    expect(container.textContent).toContain('监控商品总数')
    expect(container.textContent).toContain('120')
    expect(container.textContent).toContain('库存异常预警')
    expect(container.textContent).toContain('3')

    // 列表项渲染
    expect(container.textContent).toContain('经营日报 · 2026-11-25')
    expect(container.textContent).toContain('已完成')
    expect(container.textContent).toContain('3 项异常')
  })

  it('点击日报卡片弹出 DetailDrawer 抽屉并渲染 Markdown 报告', async () => {
    vi.spyOn(reportService, 'getLatestReport').mockResolvedValue(mockLatest)
    vi.spyOn(reportService, 'getReports').mockResolvedValue(mockList)
    vi.spyOn(reportService, 'getReport').mockResolvedValue(mockLatest)

    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted.push({ container, root })

    await act(async () => {
      root.render(<ReportsPage />)
    })

    // 找到日报卡片点击
    const itemButton = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('经营日报 · 2026-11-25'),
    )
    expect(itemButton).toBeTruthy()

    await act(async () => {
      itemButton?.click()
    })

    // 检查抽屉是否出现并渲染 Markdown 内容
    expect(document.body.textContent).toContain('今日经营核心指标')
    expect(document.body.textContent).toContain('商品销售平稳')
    expect(document.body.textContent).toContain('预警 3 款库存告急')
    expect(document.body.textContent).toContain('技术元数据与 Trace')
  })
})
