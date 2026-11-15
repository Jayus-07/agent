import { describe, expect, it } from 'vitest'
import type { SQLResultSet } from '@/lib/types'
import {
  chooseSQLChartPresentation,
  formatSQLChartValue,
  isPercentageSQLColumn,
  parseSQLChartNumber,
  sortSQLTrendRows,
} from './sqlResultPresentation'

function result(columns: string[], rows: SQLResultSet['rows']): SQLResultSet {
  return { title: '测试结果', columns, rows, total_rows: rows.length, omitted_columns: 0 }
}

describe('SQL result chart selection', () => {
  it('single numeric value becomes a metric', () => {
    expect(chooseSQLChartPresentation(result(['销售额'], [[12345.6]]))).toEqual({
      kind: 'metric', valueIndexes: [0],
    })
  })

  it('single row with multiple numeric values becomes separate metrics', () => {
    expect(chooseSQLChartPresentation(result(
      ['统计月份', '订单数', '销售额（元）'], [['2026-11', 42, '12345.60']],
    ))).toEqual({ kind: 'metrics', valueIndexes: [1, 2] })
  })

  it('date and numeric values become a line chart', () => {
    expect(chooseSQLChartPresentation(result(
      ['月份', '销售额'], [['2026-10', '1,200'], ['2026-11', '1,560']],
    ))).toEqual({ kind: 'line', categoryIndex: 0, valueIndexes: [1] })
  })

  it('recognizes Chinese year-month values as a time axis', () => {
    expect(chooseSQLChartPresentation(result(
      ['月份', '销售额'], [['2026年10月', 120], ['2026年11月', 150]],
    ))).toEqual({ kind: 'line', categoryIndex: 0, valueIndexes: [1] })
  })

  it('sorts time-series rows chronologically for chart display', () => {
    const rows = [['2026-11', 110], ['2026-09', 90], ['2026-10', 100]]
    expect(sortSQLTrendRows(rows, 0)).toEqual([
      ['2026-09', 90], ['2026-10', 100], ['2026-11', 110],
    ])
  })

  it('keeps grouped time-series results in a table instead of hiding dimensions', () => {
    expect(chooseSQLChartPresentation(result(
      ['月份', '区域', '销售额'],
      [['2026-10', '华东', 120], ['2026-11', '华南', 90]],
    )).kind).toBe('table')
  })

  it('keeps duplicate time buckets in a table instead of joining ambiguous points', () => {
    expect(chooseSQLChartPresentation(result(
      ['月份', '销售额'], [['2026-10', 120], ['2026-10', 90]],
    )).kind).toBe('table')
  })

  it('keeps trends with more series than the card can distinguish in a table', () => {
    expect(chooseSQLChartPresentation(result(
      ['月份', '销售额', '订单数', '退款额', '退款率', '客单价', '毛利'],
      [['2026-10', 120, 10, 5, 4.1, 12, 20], ['2026-11', 140, 11, 6, 4.2, 13, 22]],
    )).kind).toBe('table')
  })

  it('does not treat a lifetime value column as a date axis', () => {
    expect(chooseSQLChartPresentation(result(
      ['lifetime_value', 'revenue'], [[18, 300], [26, 350]],
    )).kind).toBe('table')
  })

  it('recognizes compact year-month values under a date-like column name', () => {
    expect(chooseSQLChartPresentation(result(
      ['orderDate', 'sales'], [[202610, 120], [202611, 150]],
    ))).toEqual({ kind: 'line', categoryIndex: 0, valueIndexes: [1] })
  })

  it('category ranks become a horizontal bar chart', () => {
    expect(chooseSQLChartPresentation(result(
      ['商品', '销量'], [['A', 30], ['B', 20]],
    ))).toEqual({ kind: 'bar', categoryIndex: 0, valueIndexes: [1] })
  })

  it('ambiguous or high-cardinality data remains a table', () => {
    expect(chooseSQLChartPresentation(result(
      ['部门', '人数', '预算'], [['研发', 10, 100], ['销售', 8, 80]],
    )).kind).toBe('table')
    expect(chooseSQLChartPresentation(result(
      ['商品', '销量'], Array.from({ length: 13 }, (_, index) => [`商品${index}`, index]),
    )).kind).toBe('table')
  })

  it('keeps incomplete numeric series in a table instead of connecting missing values', () => {
    expect(chooseSQLChartPresentation(result(
      ['月份', '销售额'], [['2026-10', 120], ['2026-11', null], ['2026-12', 150]],
    )).kind).toBe('table')
  })

  it('parses chartable numeric strings without coercing labels', () => {
    expect(parseSQLChartNumber('￥12,345.60')).toBeNull()
    expect(parseSQLChartNumber('12,345.60')).toBe(12345.6)
    expect(parseSQLChartNumber('9007199254740993')).toBeNull()
    expect(parseSQLChartNumber(9007199254740992)).toBeNull()
    expect(parseSQLChartNumber('销售12件')).toBeNull()
  })

  it('keeps an unsafe JavaScript integer in the detail table instead of charting it', () => {
    expect(chooseSQLChartPresentation(result(
      ['订单号'], [[9007199254740992]],
    )).kind).toBe('table')
  })

  it('keeps a high precision decimal in a table instead of rounding it for a chart', () => {
    const exactAmount = '1234567890123456.7800'
    const rows = [['2026-10', exactAmount], ['2026-11', exactAmount]]
    const presentation = chooseSQLChartPresentation(result(['月份', '金额'], rows))

    expect(parseSQLChartNumber(exactAmount)).toBeNull()
    expect(presentation.kind).toBe('table')
    expect(result(['月份', '金额'], rows).rows[0][1]).toBe(exactAmount)
  })

  it('preserves an explicit percent unit in chart values', () => {
    const percentages = result(
      ['月份', '退款率'], [['2026-10', '12.5%'], ['2026-11', '8%']],
    )
    const counts = result(['月份', '订单数'], [['2026-10', '12.5'], ['2026-11', '8']])

    expect(isPercentageSQLColumn(percentages, 1)).toBe(true)
    expect(isPercentageSQLColumn(counts, 1)).toBe(false)
    expect(formatSQLChartValue(12.5, true)).toBe('12.5%')
    expect(formatSQLChartValue(12.5)).toBe('12.5')
  })

  it('keeps meaningful decimal precision in chart labels and tooltips', () => {
    expect(formatSQLChartValue(0.004)).toBe('0.004')
    expect(formatSQLChartValue(123.4567)).toBe('123.4567')
  })
})
