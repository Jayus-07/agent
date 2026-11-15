'use client'

import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { BarChart3, Download, Table2 } from 'lucide-react'
import type { SQLResultSet } from '@/lib/types'
import {
  chooseSQLChartPresentation,
  formatSQLChartValue,
  isUnsafeSQLInteger,
  isPercentageSQLColumn,
  parseSQLChartNumber,
  sortSQLTrendRows,
} from './sqlResultPresentation'

const SERIES_COLORS = ['#4f46e5', '#0f766e', '#d97706', '#be123c']

function csvCell(value: string | number | boolean | null): string {
  let text = value == null ? '' : String(value)
  if (typeof value === 'string' && /^[\s]*[=+\-@\t\r]/.test(value)) text = `'${text}`
  return `"${text.replaceAll('"', '""')}"`
}

function exportCSV(result: SQLResultSet) {
  const content = [
    result.columns.map(csvCell).join(','),
    ...result.rows.map((row) => result.columns.map((_, index) => csvCell(row[index] ?? null)).join(',')),
  ].join('\r\n')
  const blob = new Blob([`\uFEFF${content}`], { type: 'text/csv;charset=utf-8' })
  const href = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  const safeTitle = result.title.replace(/[\\/:*?"<>|]/g, '-').slice(0, 50) || '查询结果'
  anchor.href = href
  anchor.download = `${safeTitle}.csv`
  anchor.click()
  window.setTimeout(() => URL.revokeObjectURL(href), 0)
}

function tableRows(result: SQLResultSet) {
  return result.rows.map((values, rowIndex) => ({
    key: `row-${rowIndex}`,
    cells: result.columns.map((_, columnIndex) => values[columnIndex] ?? null),
  }))
}

function containsUnsafeInteger(result: SQLResultSet): boolean {
  return result.rows.some((row) => row.some(isUnsafeSQLInteger))
}

function chartRows(result: SQLResultSet, categoryIndex: number, valueIndexes: number[]) {
  return sortSQLTrendRows(result.rows, categoryIndex).map((row) => ({
    category: String(row[categoryIndex] ?? '—'),
    ...Object.fromEntries(valueIndexes.map((index) => [
      `series-${index}`,
      parseSQLChartNumber(row[index]),
    ])),
  }))
}

function SQLDataTable({ result }: { result: SQLResultSet }) {
  const rows = tableRows(result)
  const hasUnsafeInteger = containsUnsafeInteger(result)
  if (rows.length === 0) {
    if (result.payload_truncated) {
      return <p className="px-3 py-8 text-center text-xs leading-5 text-slate-500">结果超出单条消息的展示上限，明细未完整载入。请缩小查询范围后重试。</p>
    }
    if (result.total_rows_known !== true) {
      return <p className="px-3 py-8 text-center text-xs leading-5 text-slate-500">暂时没有可展示的明细，无法确认是否有匹配记录。请缩小查询范围后重试。</p>
    }
    if (result.total_rows > 0) {
      return <p className="px-3 py-8 text-center text-xs leading-5 text-slate-500">本次查询返回 {result.total_rows} 行，但当前没有可展示的明细。请缩小查询范围后重试。</p>
    }
    return <p className="px-3 py-8 text-center text-xs text-slate-500">本次查询没有匹配到记录。</p>
  }
  return <>
    {hasUnsafeInteger && <p role="note" className="border-b border-amber-100 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800">
      部分整数超出浏览器可精确表示的范围，相关数值已隐藏；请重新查询以获取原始精确值。
    </p>}
    <div className="max-h-[360px] overflow-auto">
      <table className="w-full border-collapse text-left text-xs">
      <thead className="sticky top-0 z-10 bg-slate-50 text-xs text-slate-500">
        <tr>{result.columns.map((column, index) => <th key={`${column}-${index}`} className="whitespace-nowrap border-b border-slate-200 px-3 py-2 font-medium">{column}</th>)}</tr>
      </thead>
      <tbody>{rows.map((row) => <tr key={row.key} className="even:bg-slate-50/60">
        {row.cells.map((cell, index) => <td key={index} className="max-w-[260px] border-b border-slate-100 px-3 py-2 align-top text-slate-700">{cell == null ? <span className="text-slate-400">—</span> : isUnsafeSQLInteger(cell) ? <span className="text-amber-700">精度不可靠</span> : <span className="whitespace-pre-wrap break-words">{String(cell)}</span>}</td>)}
      </tr>)}</tbody>
      </table>
    </div>
  </>
}

function SQLChart({ result }: { result: SQLResultSet }) {
  const presentation = chooseSQLChartPresentation(result)
  const chartData = (presentation.kind === 'line' || presentation.kind === 'bar')
    && presentation.categoryIndex != null
    ? chartRows(result, presentation.categoryIndex, presentation.valueIndexes)
    : []

  if (presentation.kind === 'metric') {
    const index = presentation.valueIndexes[0]
    const rawValue = result.rows[0]?.[index]
    const value = parseSQLChartNumber(rawValue)
    const context = result.columns.flatMap((column, columnIndex) => (
      columnIndex !== index && result.rows[0]?.[columnIndex] != null
        ? [`${column}：${result.rows[0][columnIndex]}`]
        : []
    )).join(' · ')
    return <div className="flex min-h-40 flex-col justify-center px-5 py-6">
      <span className="text-xs font-medium text-slate-500">{result.columns[index]}</span>
      <strong className="mt-2 break-all text-3xl font-semibold tracking-tight text-slate-900">{value == null ? '—' : String(rawValue)}</strong>
      {context && <span className="mt-1 break-words text-xs text-slate-500">{context}</span>}
    </div>
  }

  if (presentation.kind === 'metrics') {
    const metricIndexes = new Set(presentation.valueIndexes)
    const context = result.columns.flatMap((column, columnIndex) => (
      !metricIndexes.has(columnIndex) && result.rows[0]?.[columnIndex] != null
        ? [`${column}：${result.rows[0][columnIndex]}`]
        : []
    )).join(' · ')
    return <div className="px-4 py-5">
      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {presentation.valueIndexes.map((index) => (
          <div key={index} className="min-w-0 rounded-lg border border-slate-100 bg-slate-50/60 px-3 py-3">
            <dt className="break-words text-xs font-medium text-slate-500">{result.columns[index]}</dt>
            <dd className="mt-1 break-all text-2xl font-semibold tracking-tight text-slate-900">
              {result.rows[0]?.[index] == null ? '—' : String(result.rows[0][index])}
            </dd>
          </div>
        ))}
      </dl>
      {context && <p className="mt-3 break-words text-xs text-slate-500">{context}</p>}
    </div>
  }

  if (presentation.kind === 'bar') {
    const valueIndex = presentation.valueIndexes[0]
    const series = `series-${valueIndex}`
    const isPercentage = isPercentageSQLColumn(result, valueIndex)
    const height = Math.min(340, Math.max(176, chartData.length * 38 + 56))
    return <div className="w-full px-2 py-4" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={chartData} layout="vertical" margin={{ top: 4, right: 22, bottom: 4, left: 8 }}>
          <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" horizontal={false} />
          <XAxis type="number" tick={{ fill: '#64748b', fontSize: 12 }} tickFormatter={(value) => formatSQLChartValue(Number(value), isPercentage)} />
          <YAxis type="category" dataKey="category" width={96} tick={{ fill: '#475569', fontSize: 12 }} />
          <Tooltip formatter={(value) => [formatSQLChartValue(Number(value), isPercentage), result.columns[valueIndex]]} />
          <Bar dataKey={series} name={result.columns[valueIndex]} fill={SERIES_COLORS[0]} radius={[0, 5, 5, 0]} maxBarSize={22} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  }

  if (presentation.kind === 'line') {
    return <div className="grid w-full gap-3 px-2 py-4 sm:grid-cols-2">
      {presentation.valueIndexes.map((index, colorIndex) => {
        const isPercentage = isPercentageSQLColumn(result, index)
        return <section key={index} aria-label={`${result.columns[index]}趋势图`} className="min-w-0">
          <h4 className="mb-1 truncate px-2 text-xs font-medium text-slate-600">{result.columns[index]}</h4>
          <div className="h-56 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 8, right: 18, bottom: 4, left: 4 }}>
                <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" />
                <XAxis dataKey="category" tick={{ fill: '#64748b', fontSize: 12 }} tickLine={false} />
                <YAxis tick={{ fill: '#64748b', fontSize: 12 }} tickFormatter={(value) => formatSQLChartValue(Number(value), isPercentage)} width={72} />
                <Tooltip formatter={(value) => [formatSQLChartValue(Number(value), isPercentage), result.columns[index]]} />
                <Line dataKey={`series-${index}`} name={`series-${index}`} type="monotone" stroke={SERIES_COLORS[colorIndex]} strokeWidth={2} dot={{ r: 2.5 }} activeDot={{ r: 4 }} connectNulls />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </section>
      })}
    </div>
  }

  return <SQLDataTable result={result} />
}

export default function SQLResultCard({ result }: { result: SQLResultSet }) {
  const displayedRows = result.rows.length
  const rowCountInconsistent = result.total_rows_known === true && result.total_rows < displayedRows
  const truncated = result.payload_truncated === true || result.total_rows > displayedRows
  // 不完整或行数矛盾的结果只展示明细，避免把局部样本画成完整趋势/排名。
  const presentation = chooseSQLChartPresentation(result)
  const chartNeedsCompleteRows = presentation.kind === 'line' || presentation.kind === 'bar'
  const chartDataIncomplete = truncated || rowCountInconsistent
    || result.omitted_columns > 0 || result.truncated_cells === true
    || (chartNeedsCompleteRows && result.total_rows_known !== true)
  const chartKind = chartDataIncomplete ? 'table' : presentation.kind
  const visualViewLabel = chartKind === 'metric' || chartKind === 'metrics' ? '指标' : '图表'
  const [view, setView] = useState<'chart' | 'table'>(chartKind === 'table' ? 'table' : 'chart')
  const returnedRowsKnown = result.total_rows_known === true && !rowCountInconsistent
  const hasUnsafeInteger = containsUnsafeInteger(result)
  const effectiveView = chartKind === 'table' ? 'table' : view
  const countLabel = returnedRowsKnown
    ? truncated ? `展示 ${displayedRows} 行 · 查询返回 ${result.total_rows} 行` : `查询返回 ${result.total_rows} 行`
    : displayedRows > 0
      ? `展示 ${displayedRows} 行 · ${rowCountInconsistent ? '行数信息不一致' : '查询返回数未确认'}`
      : rowCountInconsistent ? '行数信息不一致，无法确认返回数' : '查询返回数未确认'

  return <section aria-label={`${result.title}数据结果`} className="mt-4 overflow-hidden rounded-xl border border-slate-200 bg-white">
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-slate-100 px-3.5 py-3 sm:px-4">
      <div className="min-w-0 flex-1">
        <h3 className="truncate text-sm font-semibold text-slate-800">{result.title}</h3>
        <p className="mt-0.5 text-xs text-slate-500">{countLabel}{result.omitted_columns ? ` · 另有 ${result.omitted_columns} 列未展示` : ''}{result.truncated_cells ? ' · 部分单元格已截短' : ''}</p>
      </div>
      <div className="flex items-center gap-1">
        <button type="button" onClick={() => setView('chart')} disabled={chartKind === 'table'} aria-pressed={effectiveView === 'chart'} className={`inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-40 ${effectiveView === 'chart' ? 'bg-indigo-50 text-indigo-700' : 'text-slate-500 hover:bg-slate-50'}`}><BarChart3 size={13} />{visualViewLabel}</button>
        <button type="button" onClick={() => setView('table')} aria-pressed={effectiveView === 'table'} className={`inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs font-medium transition ${effectiveView === 'table' ? 'bg-indigo-50 text-indigo-700' : 'text-slate-500 hover:bg-slate-50'}`}><Table2 size={13} />明细</button>
        <button type="button" title={hasUnsafeInteger ? '存在精度不可确认的整数，无法准确导出' : '导出当前展示的明细'} onClick={() => exportCSV(result)} disabled={displayedRows === 0 || hasUnsafeInteger} className="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs font-medium text-slate-500 transition hover:bg-slate-50 hover:text-slate-800 disabled:cursor-not-allowed disabled:opacity-40"><Download size={13} />CSV</button>
      </div>
    </div>
    {rowCountInconsistent && <p role="note" className="border-b border-amber-100 bg-amber-50 px-3.5 py-2 text-xs leading-5 text-amber-800 sm:px-4">
      查询返回行数少于已展示的明细，无法确认本次查询返回量。
    </p>}
    {effectiveView === 'chart' ? <SQLChart result={result} /> : <SQLDataTable result={result} />}
    {truncated && <p className="border-t border-slate-100 px-3.5 py-2 text-xs text-slate-500 sm:px-4">当前只展示前 {displayedRows} 行；缩小查询范围可查看更完整的明细。</p>}
  </section>
}
