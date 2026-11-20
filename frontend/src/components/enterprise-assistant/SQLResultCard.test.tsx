import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { renderToStaticMarkup } from 'react-dom/server'
import type { SQLResultSet } from '@/lib/types'
import SQLResultCard from './SQLResultCard'

const mounted: { container: HTMLElement; root: Root }[] = []
let originalCreateObjectURLDescriptor: PropertyDescriptor | undefined
let createObjectURLWasPatched = false

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
  if (createObjectURLWasPatched) {
    if (originalCreateObjectURLDescriptor) {
      Object.defineProperty(URL, 'createObjectURL', originalCreateObjectURLDescriptor)
    } else {
      Reflect.deleteProperty(URL, 'createObjectURL')
    }
    originalCreateObjectURLDescriptor = undefined
    createObjectURLWasPatched = false
  }
  vi.restoreAllMocks()
})

function renderCard(result: SQLResultSet): string {
  return renderToStaticMarkup(createElement(SQLResultCard, { result }))
}

describe('SQLResultCard', () => {
  it('keeps result metadata and controls readable', () => {
    const html = renderCard({
      title: '本月销售额', columns: ['销售额'], rows: [[12345.6]],
      total_rows: 1, omitted_columns: 0,
    })

    expect(html).toContain('truncate text-sm font-semibold text-slate-800')
    expect(html).toContain('basis-full flex-1 sm:basis-auto')
    expect(html).toContain('mt-0.5 text-xs text-slate-500')
    expect(html).toContain('text-xs font-medium transition')
    expect(html).not.toContain('text-[10px]')
  })

  it('renders a metric and controls for a single-value result', () => {
    const html = renderCard({
      title: '本月销售额', columns: ['销售额'], rows: [[12345.6]],
      total_rows: 1, omitted_columns: 0,
    })

    expect(html).toContain('本月销售额')
    expect(html).toContain('12345.6')
    expect(html).toContain('aria-label="本月销售额数据结果"')
    expect(html).toContain('CSV')
  })

  it('localizes a technical metric alias and a generic database title', () => {
    const html = renderCard({
      title: '数据库查询', columns: ['active_product_count'], rows: [[54]],
      total_rows: 1, total_rows_known: true, omitted_columns: 0,
    })

    expect(html).toContain('在售商品数')
    expect(html).not.toContain('active_product_count')
    expect(html).not.toContain('数据库查询')
  })

  it('shows a refund-rate metric as a percentage and keeps its entity context', () => {
    const html = renderCard({
      title: '退款率最高的商品', columns: ['product_name', 'refund_rate'],
      rows: [['无线耳机', '1.0000']], total_rows: 1, omitted_columns: 0,
    })

    expect(html).toContain('100%')
    expect(html).toContain('商品：无线耳机')
    expect(html).not.toContain('1.0000')
  })

  it('shows an explicit growth percentage at the user-facing scale', () => {
    const html = renderCard({
      title: '本月销售额较上月同期', columns: ['mom_growth_percent'],
      rows: [['-42.47']], total_rows: 1, omitted_columns: 0,
    })

    expect(html).toContain('-42.47%')
  })

  it('keeps an unavailable growth percentage visible when the comparison base is zero', () => {
    const html = renderCard({
      title: '本月销售额较上月同期',
      columns: ['current_month_sales', 'previous_period_sales', 'mom_growth_percent'],
      rows: [[1200, 0, null]], total_rows: 1, omitted_columns: 0,
    })

    expect(html).toContain('增长百分比')
    expect(html).toContain('<dd class="mt-1 break-all text-2xl font-semibold tracking-tight text-slate-900">—</dd>')
  })

  it('uses the same percentage unit in detail tables', () => {
    const html = renderCard({
      title: '按地区拆分的退款率', columns: ['月份', 'refund_rate', '退款笔数', '地区'],
      rows: [['2026-10', '0.0816', 4, '华东'], ['2026-11', '0.0960', 5, '华南']],
      total_rows: 2, total_rows_known: true, omitted_columns: 0,
    })

    expect(html).toContain('8.16%')
    expect(html).toContain('9.6%')
    expect(html).not.toContain('0.0816')
  })

  it('exports percentage values with the same unit shown in the card', async () => {
    originalCreateObjectURLDescriptor = Object.getOwnPropertyDescriptor(URL, 'createObjectURL')
    createObjectURLWasPatched = true
    let exportedBlob: Blob | undefined
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: (blob: Blob) => {
        exportedBlob = blob
        return 'blob:sql-result-test'
      },
    })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted.push({ container, root })

    await act(async () => {
      root.render(createElement(SQLResultCard, {
        result: {
          title: '退款率', columns: ['refund_rate'], rows: [['0.0816']],
          total_rows: 1, omitted_columns: 0,
        },
      }))
    })
    const csvButton = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('CSV'))
    await act(async () => { csvButton?.click() })

    expect(click).toHaveBeenCalledOnce()
    expect(exportedBlob).toBeDefined()
    await expect(exportedBlob?.text()).resolves.toContain('退款率（%）')
    await expect(exportedBlob?.text()).resolves.toContain('8.16%')
  })

  it('renders multiple single-row metrics separately and keeps their context', () => {
    const html = renderCard({
      title: '本月经营概览', columns: ['统计月份', '订单数', '销售额（元）'],
      rows: [['2026-11', 42, '12345.60']], total_rows: 1, omitted_columns: 0,
    })

    expect(html).toContain('<dl')
    expect(html).toContain('订单数')
    expect(html).toContain('42')
    expect(html).toContain('销售额（元）')
    expect(html).toContain('12345.60')
    expect(html).toContain('统计月份：2026-11')
    expect(html).toContain('>指标</button>')
  })

  it('shows a readable table when automatic chart selection is uncertain', () => {
    const html = renderCard({
      title: '分类明细', columns: ['部门', '人数', '预算'],
      rows: [['研发', 10, 100], ['销售', 8, 80]],
      total_rows: 2, total_rows_known: true, omitted_columns: 0,
    })

    expect(html).toContain('<table')
    expect(html).toContain('研发')
    expect(html).toContain('预算')
    expect(html).toContain('查询返回 2 行')
  })

  it('hides an unsafe JavaScript integer and explains the precision issue', () => {
    const html = renderCard({
      title: '订单编号', columns: ['订单号'], rows: [[9007199254740992]],
      total_rows: 1, omitted_columns: 0,
    })

    expect(html).toContain('<table')
    expect(html).toContain('相关数值已隐藏')
    expect(html).toContain('精度不可靠')
    expect(html).not.toContain('9007199254740992')
    expect(html).toContain('存在精度不可确认的整数，无法准确导出')
  })

  it('shows exact high precision decimals in the detail table', () => {
    const amount = '1234567890123456.7800'
    const html = renderCard({
      title: '月度金额', columns: ['月份', '金额'],
      rows: [['2026-10', amount], ['2026-11', amount]],
      total_rows: 2, omitted_columns: 0,
    })

    expect(html).toContain('<table')
    expect(html).toContain(amount)
    expect(html).toContain('2026-10')
  })

  it('separates time-series metrics into charts with independent scales and labels', () => {
    const html = renderCard({
      title: '销售与退款趋势',
      columns: ['月份', '销售额（元）', '退款率'],
      rows: [
        ['2026-10', 125000, '1.2%'],
        ['2026-11', 148000, '0.8%'],
      ],
      total_rows: 2,
      total_rows_known: true,
      omitted_columns: 0,
    })

    expect(html).toContain('销售额（元）趋势图')
    expect(html).toContain('退款率趋势图')
    expect(html).toContain('销售额（元）')
    expect(html).toContain('退款率')
  })

  it('keeps a partial time-series preview in a table instead of presenting a full trend chart', () => {
    const html = renderCard({
      title: '销售额趋势', columns: ['月份', '销售额'],
      rows: [['2026-10', 125000], ['2026-11', 148000]],
      total_rows: 12, total_rows_known: true, omitted_columns: 0,
    })

    expect(html).toContain('<table')
    expect(html).toContain('展示 2 行 · 查询返回 12 行')
    expect(html).not.toContain('销售额趋势图')
    expect(html).toContain('当前只展示前 2 行')
  })

  it.each([
    {
      label: '排名', columns: ['商品', '销量'],
      rows: [['A', 30], ['B', 20]],
    },
    {
      label: '趋势', columns: ['月份', '销售额'],
      rows: [['2026-10', 30], ['2026-11', 20]],
    },
  ])('keeps $label results in a table when the returned row count is unknown', ({
    label, columns, rows,
  }) => {
    const html = renderCard({
      title: `${label}结果`, columns, rows,
      total_rows: rows.length, total_rows_known: false, omitted_columns: 0,
    })

    expect(html).toContain('<table')
    expect(html).toContain('disabled="" aria-pressed="false"')
    expect(html).toContain('查询返回数未确认')
  })

  it('uses a table when result columns were omitted from the chart payload', () => {
    const html = renderCard({
      title: '销售额趋势', columns: ['月份', '销售额'],
      rows: [['2026-10', 30], ['2026-11', 20]],
      total_rows: 2, total_rows_known: true, omitted_columns: 1,
    })

    expect(html).toContain('<table')
    expect(html).toContain('disabled="" aria-pressed="false"')
    expect(html).toContain('另有 1 列未展示')
  })

  it('uses a table when chart cell values were truncated', () => {
    const html = renderCard({
      title: '商品销量', columns: ['商品', '销量'],
      rows: [['商品 A', 30], ['商品 B', 20]],
      total_rows: 2, total_rows_known: true, omitted_columns: 0,
      truncated_cells: true,
    })

    expect(html).toContain('<table')
    expect(html).toContain('disabled="" aria-pressed="false"')
    expect(html).toContain('部分单元格已截短')
  })

  it('does not describe a payload-truncated result as having no matches', () => {
    const html = renderCard({
      title: '大结果', columns: ['商品'], rows: [], total_rows: 200,
      omitted_columns: 0, payload_truncated: true,
    })

    expect(html).toContain('结果超出单条消息的展示上限')
    expect(html).not.toContain('本次查询没有匹配到记录')
  })

  it('does not describe a non-empty total with no preview rows as no matches', () => {
    const html = renderCard({
      title: '订单查询', columns: ['订单号'], rows: [], total_rows: 12,
      total_rows_known: true, omitted_columns: 0,
    })

    expect(html).toContain('本次查询返回 12 行')
    expect(html).toContain('没有可展示的明细')
    expect(html).not.toContain('本次查询没有匹配到记录')
  })

  it('does not claim no matches when the total count is unknown', () => {
    const html = renderCard({
      title: '订单查询', columns: ['订单号'], rows: [], total_rows: 0,
      total_rows_known: false, omitted_columns: 0,
    })

    expect(html).toContain('查询返回数未确认')
    expect(html).toContain('无法确认是否有匹配记录')
    expect(html).not.toContain('本次查询没有匹配到记录')
  })

  it('treats a missing total-count confirmation as unknown for empty legacy results', () => {
    const html = renderCard({
      title: '历史查询', columns: ['订单号'], rows: [], total_rows: 0,
      omitted_columns: 0,
    })

    expect(html).toContain('查询返回数未确认')
    expect(html).toContain('无法确认是否有匹配记录')
    expect(html).not.toContain('本次查询没有匹配到记录')
  })

  it('does not present an unconfirmed row-count value as authoritative', () => {
    const html = renderCard({
      title: '历史查询', columns: ['订单号'], rows: [['A-1']], total_rows: 1,
      omitted_columns: 0,
    })

    expect(html).toContain('展示 1 行 · 查询返回数未确认')
    expect(html).not.toContain('共 1 行')
  })

  it('marks a confirmed total that contradicts visible rows as inconsistent', () => {
    const html = renderCard({
      title: '订单查询', columns: ['订单号'], rows: [['A-1'], ['A-2']],
      total_rows: 1, total_rows_known: true, omitted_columns: 0,
    })

    expect(html).toContain('展示 2 行 · 行数信息不一致')
    expect(html).toContain('查询返回行数少于已展示的明细，无法确认本次查询返回量。')
    expect(html).not.toContain('共 1 行')
  })

  it('confirms no matches only when the backend reports a known zero total', () => {
    const html = renderCard({
      title: '订单查询', columns: ['订单号'], rows: [], total_rows: 0,
      total_rows_known: true, omitted_columns: 0,
    })

    expect(html).toContain('本次查询没有匹配到记录')
    expect(html).not.toContain('查询返回数未确认')
  })

  it('switches from an automatically selected chart to the matching detail table', async () => {
    const result: SQLResultSet = {
      title: '商品销量', columns: ['商品', '销量'], rows: [['A', 30], ['B', 20]],
      total_rows: 2, total_rows_known: true, omitted_columns: 0,
    }
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted.push({ container, root })
    await act(async () => { root.render(createElement(SQLResultCard, { result })) })
    expect(container.querySelector('table')).toBeNull()

    const detailsButton = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('明细'))
    expect(detailsButton).toBeDefined()
    await act(async () => { detailsButton?.click() })

    expect(container.querySelector('table')).not.toBeNull()
    expect(container.textContent).toContain('A')
    expect(container.textContent).toContain('30')
  })

  it('forces detail view if an updated result becomes truncated after chart selection', async () => {
    const complete: SQLResultSet = {
      title: '销售额趋势', columns: ['月份', '销售额'],
      rows: [['2026-10', 125000], ['2026-11', 148000]],
      total_rows: 2, total_rows_known: true, omitted_columns: 0,
    }
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted.push({ container, root })

    await act(async () => { root.render(createElement(SQLResultCard, { result: complete })) })
    expect(container.querySelector('table')).toBeNull()

    const truncated = { ...complete, total_rows: 12 }
    await act(async () => { root.render(createElement(SQLResultCard, { result: truncated })) })

    expect(container.querySelector('table')).not.toBeNull()
    expect(container.textContent).toContain('展示 2 行 · 查询返回 12 行')
    expect(container.textContent).not.toContain('销售额趋势图')
  })
})
