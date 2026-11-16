import type { SQLResultSet } from '@/lib/types'

export type SQLChartKind = 'metric' | 'metrics' | 'line' | 'bar' | 'table'

export interface SQLChartPresentation {
  kind: SQLChartKind
  categoryIndex?: number
  valueIndexes: number[]
}

const DATE_COLUMN = /(日期|时间|年月|月份|(?:^|[^a-z])(?:period|date|datetime|timestamp|month|week|day|year)(?=$|[^a-z]))/i
const DATE_VALUE = /^(?:\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?(?:[ T]\d{1,2}:\d{2})?|\d{4}年\d{1,2}月(?:\d{1,2}日?)?(?:[ T]\d{1,2}:\d{2})?)$/
const COMPACT_DATE_VALUE = /^\d{6}(?:\d{2})?$/
const NUMERIC_VALUE = /^[+-]?(?:(?:\d{1,3}(?:,\d{3})+)|\d+)(?:\.\d+)?%?$/

export function isUnsafeSQLInteger(value: unknown): value is number {
  return typeof value === 'number'
    && Number.isInteger(value)
    && !Number.isSafeInteger(value)
}

export function parseSQLChartNumber(value: unknown): number | null {
  if (typeof value === 'number') {
    return Number.isFinite(value) && !isUnsafeSQLInteger(value) ? value : null
  }
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  if (!NUMERIC_VALUE.test(trimmed)) return null
  const normalized = trimmed.replaceAll(',', '').replace(/%$/, '')
  const significantDigits = normalized.replace('.', '').replace(/^[-+]?0*/, '').length
  if (significantDigits > 15) return null
  const parsed = Number(normalized)
  return Number.isFinite(parsed) ? parsed : null
}

function parseSQLDateSortValue(value: unknown): number | null {
  const normalized = String(value).trim()
  let year: number
  let month: number
  let day = 1
  let hour = 0
  let minute = 0
  if (COMPACT_DATE_VALUE.test(normalized)) {
    year = Number(normalized.slice(0, 4))
    month = Number(normalized.slice(4, 6))
    if (normalized.length === 8) day = Number(normalized.slice(6, 8))
  } else {
    const westernDate = /^(\d{4})[-/](\d{1,2})(?:[-/](\d{1,2}))?(?:[ T](\d{1,2}):(\d{2}))?$/.exec(normalized)
    const chineseDate = /^(\d{4})年(\d{1,2})月(?:(\d{1,2})日?)?(?:[ T](\d{1,2}):(\d{2}))?$/.exec(normalized)
    const parts = westernDate || chineseDate
    if (!parts) return null
    year = Number(parts[1])
    month = Number(parts[2])
    day = Number(parts[3] || 1)
    hour = Number(parts[4] || 0)
    minute = Number(parts[5] || 0)
  }

  if (month < 1 || month > 12 || hour > 23 || minute > 59) return null
  const validDay = new Date(Date.UTC(year, month, 0)).getUTCDate()
  if (day < 1 || day > validDay) return null

  return year * 100_000_000 + month * 1_000_000 + day * 10_000 + hour * 100 + minute
}

function isDateColumn(result: SQLResultSet, index: number): boolean {
  const label = (result.columns[index] || '').replace(/([a-z])([A-Z])/g, '$1 $2')
  const dateNamed = DATE_COLUMN.test(label)
  const populated = result.rows.map((row) => row[index]).filter((value) => value != null)
  return populated.length === result.rows.length && populated.length > 0 && populated.every((value) => {
    const normalized = String(value).trim()
    return (DATE_VALUE.test(normalized) || (dateNamed && COMPACT_DATE_VALUE.test(normalized)))
      && parseSQLDateSortValue(normalized) != null
  })
}

export function sortSQLTrendRows(rows: SQLResultSet['rows'], categoryIndex: number): SQLResultSet['rows'] {
  return rows
    .map((row, originalIndex) => ({
      row,
      originalIndex,
      dateValue: parseSQLDateSortValue(row[categoryIndex]),
    }))
    .sort((left, right) => (
      (left.dateValue ?? 0) - (right.dateValue ?? 0)
      || left.originalIndex - right.originalIndex
    ))
    .map(({ row }) => row)
}

function numericIndexes(result: SQLResultSet): number[] {
  return result.columns.flatMap((_, index) => {
    const values = result.rows.map((row) => row[index])
    // 缺失值会让趋势图插值连接，或让柱状图静默忽略某些行；不完整列回退明细表。
    if (!values.length || values.some((value) => value == null || parseSQLChartNumber(value) == null)) return []
    return [index]
  })
}

export function isPercentageSQLColumn(result: SQLResultSet, index: number): boolean {
  const values = result.rows.map((row) => row[index])
  return values.length > 0
    && values.every((value) => typeof value === 'string' && value.trim().endsWith('%'))
}

/** 只在字段含义足够明确时画图；不确定时回退明细表。 */
export function chooseSQLChartPresentation(result: SQLResultSet): SQLChartPresentation {
  const values = numericIndexes(result)
  if (result.rows.length === 1 && values.length > 0) {
    return { kind: values.length === 1 ? 'metric' : 'metrics', valueIndexes: values }
  }

  const dateIndexes = result.columns.flatMap((_, index) => isDateColumn(result, index) ? [index] : [])
  if (dateIndexes.length === 1) {
    const dateIndex = dateIndexes[0]
    const valueIndexes = values.filter((index) => index !== dateIndex)
    const hasHiddenDimension = result.columns.some((_, index) => (
      index !== dateIndex && !valueIndexes.includes(index)
    ))
    const dateValues = result.rows.map((row) => parseSQLDateSortValue(row[dateIndex]))
    const hasUniqueDates = dateValues.every((value) => value != null)
      && new Set(dateValues).size === dateValues.length
    if (valueIndexes.length > 0 && valueIndexes.length <= 4 && !hasHiddenDimension && hasUniqueDates) {
      return {
        kind: 'line',
        categoryIndex: dateIndex,
        valueIndexes,
      }
    }
  }
  if (dateIndexes.length > 0) return { kind: 'table', valueIndexes: [] }

  if (result.columns.length === 2 && values.length === 1
      && result.rows.length <= 12 && values[0] !== 0) {
    return { kind: 'bar', categoryIndex: 1 - values[0], valueIndexes: values }
  }

  return { kind: 'table', valueIndexes: [] }
}

export function formatSQLChartNumber(value: number): string {
  // 保留有意义的小数位，避免 0.004 这类结果在坐标轴/悬浮提示中被显示成 0。
  return new Intl.NumberFormat('zh-CN', { maximumSignificantDigits: 15 }).format(value)
}

export function formatSQLChartValue(value: number, isPercentage = false): string {
  return `${formatSQLChartNumber(value)}${isPercentage ? '%' : ''}`
}
