'use client'

/**
 * 数据查询页（2026-10-01）
 *
 * 双模式：
 *  - 问数据：自然语言 → SQL（NL2SQL，与用户端聊天同一 SQLAgent 策略链），
 *    展示生成的 SQL 供人工核对；
 *  - 浏览表：传统表分页查看（服务端拼装只读 SELECT，无 LLM），作为
 *    NL2SQL 回答正确性的核对基准。
 *
 * 权限：sql.read（viewer 无 → 后端 403）；SQL_AGENT_ENABLED=false → 503。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Database,
  MessageSquareText, RefreshCw, Search, Table2,
} from 'lucide-react'
import {
  dataQueryService,
  type BrowseTable,
  type SqlQueryResponse,
  type TableBrowseResponse,
} from '@/api/dataQuery'

type Tab = 'ask' | 'browse'

/** 快捷示例（覆盖商品/订单/库存/竞品四域，供一键填入） */
const EXAMPLES = [
  '查询库存低于安全库存的商品及其库存量',
  '统计各商品分类的销售额排名',
  '列出最近 10 个订单的金额',
  '对比竞品价格与我们售价的差异',
]

const STATUS_BADGE: Record<string, string> = {
  success: 'bg-green-50 text-green-700 border-green-200',
  no_data: 'bg-yellow-50 text-yellow-700 border-yellow-200',
}
const STATUS_LABEL: Record<string, string> = {
  success: '成功', no_data: '无数据', failed: '执行失败',
  timeout: '超时', syntax_error: '语法错误', permission_denied: '权限拒绝',
  validation_error: '未通过安全校验', no_table: '未匹配到数据表',
}

/** 列头显示名：中文注释主显、物理列名进 title 悬浮（核对 SQL 时两边对得上） */
function columnHeaderLabel(col: string, comments?: Record<string, string>): { label: string; tip: string } {
  const comment = comments?.[col]
  return comment
    ? { label: comment, tip: `${comment} (${col})` }
    : { label: col, tip: col }
}

/** NL2SQL / 浏览通用的结果表渲染（rows 已由后端脱敏） */
function ResultTable({ columns, rows, comments }: {
  columns: string[]
  rows: Record<string, unknown>[]
  comments?: Record<string, string>
}) {
  if (!columns.length) return null
  return (
    <div className="overflow-x-auto border border-border-subtle rounded-lg">
      <table className="w-full text-xs">
        <thead>
          <tr className="bg-surface-elevated border-b border-border-subtle">
            {columns.map((c) => {
              const { label, tip } = columnHeaderLabel(c, comments)
              return (
                <th key={c} title={tip} className="px-3 py-2 text-left font-medium text-text-muted whitespace-nowrap">{label}</th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b border-border-subtle last:border-0 hover:bg-surface-hover">
              {columns.map((c) => (
                <td key={c} className="px-3 py-1.5 text-text-primary whitespace-nowrap max-w-64 truncate" title={String(row[c] ?? '')}>
                  {row[c] === null || row[c] === undefined ? <span className="text-text-muted">NULL</span> : String(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_BADGE[status] ?? 'bg-red-50 text-red-700 border-red-200'
  return (
    <span className={`px-2 py-0.5 rounded border text-xs ${cls}`}>
      {STATUS_LABEL[status] ?? status}
    </span>
  )
}

/** NL2SQL 结果区：状态 + 耗时 + 可折叠 SQL + 结果表 */
function AskResult({ result }: { result: SqlQueryResponse }) {
  const [showSql, setShowSql] = useState(false)
  return (
    <div className="mt-4 space-y-3">
      <div className="flex items-center gap-3 text-xs text-text-muted">
        <StatusBadge status={result.status} />
        <span>耗时 {(result.elapsed_sec * 1000).toFixed(0)} ms</span>
        <span>{result.row_count} 行</span>
        {result.sql && (
          <button onClick={() => setShowSql((v) => !v)} className="flex items-center gap-1 hover:text-text-primary transition-colors">
            {showSql ? <ChevronUp size={12} /> : <ChevronDown size={12} />} 生成的 SQL
          </button>
        )}
      </div>
      {result.error && (
        <div className="text-xs text-red-600 border border-red-200 bg-red-50 rounded-lg px-3 py-2">
          {result.error}
        </div>
      )}
      {showSql && result.sql && (
        <pre className="text-xs bg-surface-elevated border border-border-subtle rounded-lg p-3 overflow-x-auto whitespace-pre-wrap">{result.sql}</pre>
      )}
      {result.columns.length > 0 && (
        <ResultTable columns={result.columns} rows={result.rows} comments={result.column_comments} />
      )}
    </div>
  )
}

/** 浏览模式的单表数据区：排序表头 */
function BrowseResult({
  result, sort, order, onSort,
}: {
  result: TableBrowseResponse
  sort: string | null
  order: 'asc' | 'desc'
  onSort: (col: string) => void
}) {
  return (
    <div className="mt-4 space-y-3">
      <div className="flex items-center gap-3 text-xs text-text-muted">
        <StatusBadge status={result.status} />
        <span>共 {result.total} 行</span>
        <span>耗时 {(result.elapsed_sec * 1000).toFixed(0)} ms</span>
      </div>
      {result.error && (
        <div className="text-xs text-red-600 border border-red-200 bg-red-50 rounded-lg px-3 py-2">{result.error}</div>
      )}
      <div className="overflow-x-auto border border-border-subtle rounded-lg">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-surface-elevated border-b border-border-subtle">
              {result.columns.map((c) => {
                const { label, tip } = columnHeaderLabel(c, result.column_comments)
                return (
                  <th key={c} className="px-3 py-2 text-left font-medium text-text-muted whitespace-nowrap">
                    <button onClick={() => onSort(c)} title={tip} className="flex items-center gap-1 hover:text-text-primary transition-colors">
                      {label}
                      {sort === c
                        ? (order === 'asc' ? <ChevronUp size={11} /> : <ChevronDown size={11} />)
                        : <ChevronUp size={11} className="opacity-20" />}
                    </button>
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {result.rows.length === 0 ? (
              <tr><td colSpan={result.columns.length} className="px-3 py-6 text-center text-text-muted">本页无数据</td></tr>
            ) : result.rows.map((row, i) => (
              <tr key={i} className="border-b border-border-subtle last:border-0 hover:bg-surface-hover">
                {result.columns.map((c) => (
                  <td key={c} className="px-3 py-1.5 text-text-primary whitespace-nowrap max-w-64 truncate" title={String(row[c] ?? '')}>
                    {row[c] === null || row[c] === undefined ? <span className="text-text-muted">NULL</span> : String(row[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default function DataExplorerPage() {
  const [tab, setTab] = useState<Tab>('ask')
  const [catalog, setCatalog] = useState<BrowseTable[]>([])
  const [catalogError, setCatalogError] = useState<string | null>(null)
  const [loadingCatalog, setLoadingCatalog] = useState(true)

  // ── 问数据（NL2SQL）──
  const [question, setQuestion] = useState('')
  const [asking, setAsking] = useState(false)
  const [askResult, setAskResult] = useState<SqlQueryResponse | null>(null)
  const [askError, setAskError] = useState<string | null>(null)

  // ── 浏览表 ──
  const [selected, setSelected] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize] = useState(20)
  const [sort, setSort] = useState<string | null>(null)
  const [order, setOrder] = useState<'asc' | 'desc'>('asc')
  const [browse, setBrowse] = useState<TableBrowseResponse | null>(null)
  const [browseLoading, setBrowseLoading] = useState(false)
  const [browseError, setBrowseError] = useState<string | null>(null)

  const loadCatalog = useCallback(async () => {
    setLoadingCatalog(true)
    setCatalogError(null)
    try {
      const res = await dataQueryService.getCatalog()
      setCatalog(res.tables)
    } catch (e) {
      setCatalogError(e instanceof Error ? e.message : '加载表目录失败')
    } finally {
      setLoadingCatalog(false)
    }
  }, [])

  useEffect(() => { loadCatalog() }, [loadCatalog])

  // 按 schema 分组（目录顺序即 schema_loader 排序）
  const grouped = useMemo(() => {
    const map = new Map<string, BrowseTable[]>()
    for (const t of catalog) {
      const list = map.get(t.schema_name) ?? []
      list.push(t)
      map.set(t.schema_name, list)
    }
    return [...map.entries()]
  }, [catalog])

  const loadBrowse = useCallback(async (qualified: string, opts: { page: number; sort: string | null; order: 'asc' | 'desc' }) => {
    setBrowseLoading(true)
    setBrowseError(null)
    try {
      const res = await dataQueryService.browseTable(qualified, {
        page: opts.page, pageSize, sort: opts.sort ?? undefined, order: opts.order,
      })
      setBrowse(res)
    } catch (e) {
      setBrowseError(e instanceof Error ? e.message : '加载表数据失败')
      setBrowse(null)
    } finally {
      setBrowseLoading(false)
    }
  }, [pageSize])

  function selectTable(qualified: string) {
    setSelected(qualified)
    setPage(1)
    setSort(null)
    setOrder('asc')
    setBrowse(null)
    loadBrowse(qualified, { page: 1, sort: null, order: 'asc' })
  }

  function changePage(next: number) {
    if (!selected) return
    setPage(next)
    loadBrowse(selected, { page: next, sort, order })
  }

  function toggleSort(col: string) {
    if (!selected) return
    const nextOrder = sort === col && order === 'asc' ? 'desc' : 'asc'
    setSort(col)
    setOrder(nextOrder)
    setPage(1)
    loadBrowse(selected, { page: 1, sort: col, order: nextOrder })
  }

  async function ask(q: string) {
    const trimmed = q.trim()
    if (!trimmed || asking) return
    setAsking(true)
    setAskError(null)
    setAskResult(null)
    try {
      setAskResult(await dataQueryService.runQuery(trimmed))
    } catch (e) {
      setAskError(e instanceof Error ? e.message : '查询失败')
    } finally {
      setAsking(false)
    }
  }

  const selectedTable = catalog.find((t) => t.qualified_name === selected)
  const totalPages = browse ? Math.max(1, Math.ceil(browse.total / browse.page_size)) : 1

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-6xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">数据查询</h1>
            <p className="text-xs text-text-muted mt-1">
              自然语言问答 + 表分页浏览 · 只读 · 与用户端同一安全栈（白名单 / 脱敏 / 审计）
            </p>
          </div>
          <button onClick={loadCatalog} className="flex items-center gap-1.5 text-xs text-text-muted hover:text-text-primary transition-colors">
            <RefreshCw size={14} /> 刷新目录
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mb-6 border-b border-border-subtle">
          {([
            { key: 'ask', label: '问数据', icon: <MessageSquareText size={14} /> },
            { key: 'browse', label: '浏览表', icon: <Table2 size={14} /> },
          ] as const).map(({ key, label, icon }) => (
            <button key={key} onClick={() => setTab(key)}
              className={`flex items-center gap-1.5 px-4 py-2 text-sm border-b-2 -mb-px transition-colors ${
                tab === key
                  ? 'border-accent text-accent font-medium'
                  : 'border-transparent text-text-muted hover:text-text-primary'
              }`}>
              {icon} {label}
            </button>
          ))}
        </div>

        {tab === 'ask' && (
          <div>
            <div className="flex gap-2">
              <input
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') ask(question) }}
                placeholder="用自然语言描述要查的数据，例如：统计库存低于安全库存的商品"
                className="flex-1 px-3 py-2 text-sm border border-border-subtle rounded-lg bg-surface-base text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent/50"
              />
              <button onClick={() => ask(question)} disabled={asking || !question.trim()}
                className="flex items-center gap-1.5 px-4 py-2 text-sm bg-accent text-white rounded-lg disabled:opacity-40 hover:opacity-90 transition-opacity">
                <Search size={14} /> {asking ? '查询中…' : '查询'}
              </button>
            </div>
            <div className="flex flex-wrap gap-2 mt-3">
              {EXAMPLES.map((ex) => (
                <button key={ex} onClick={() => { setQuestion(ex); ask(ex) }}
                  className="px-2.5 py-1 text-xs text-text-muted border border-border-subtle rounded-full hover:text-text-primary hover:border-text-muted transition-colors">
                  {ex}
                </button>
              ))}
            </div>
            {askError && (
              <div className="mt-4 text-xs text-red-600 border border-red-200 bg-red-50 rounded-lg px-3 py-2">{askError}</div>
            )}
            {askResult && <AskResult result={askResult} />}
          </div>
        )}

        {tab === 'browse' && (
          <div className="grid grid-cols-4 gap-4">
            {/* 左：表目录（按 schema 分组） */}
            <div className="col-span-1 border border-border-subtle rounded-lg overflow-y-auto max-h-[70vh]">
              {loadingCatalog && <div className="p-4 text-xs text-text-muted">加载目录…</div>}
              {catalogError && (
                <div className="p-3 text-xs text-red-600">
                  {catalogError}
                  <button onClick={loadCatalog} className="block mt-2 underline">重试</button>
                </div>
              )}
              {grouped.map(([schema, tables]) => (
                <div key={schema}>
                  <div className="flex items-center gap-1.5 px-3 py-2 text-xs font-medium text-text-muted bg-surface-elevated border-b border-border-subtle sticky top-0">
                    <Database size={12} /> {schema}
                  </div>
                  {tables.map((t) => (
                    <button key={t.qualified_name} onClick={() => selectTable(t.qualified_name)}
                      title={t.description}
                      className={`block w-full text-left px-3 py-1.5 text-xs truncate transition-colors ${
                        selected === t.qualified_name
                          ? 'bg-accent-soft text-text-primary font-medium'
                          : 'text-text-muted hover:bg-surface-elevated hover:text-text-primary'
                      }`}>
                      {t.name}
                    </button>
                  ))}
                </div>
              ))}
            </div>

            {/* 右：表数据 */}
            <div className="col-span-3">
              {!selected && (
                <div className="text-xs text-text-muted border border-dashed border-border-subtle rounded-lg px-4 py-10 text-center">
                  从左侧选择一张业务表开始浏览
                </div>
              )}
              {selected && (
                <>
                  <div className="mb-2">
                    <div className="text-sm font-medium text-text-primary">{selected}</div>
                    {selectedTable?.description && (
                      <div className="text-xs text-text-muted mt-0.5">{selectedTable.description}</div>
                    )}
                  </div>
                  {browseLoading && <div className="text-xs text-text-muted">加载中…</div>}
                  {browseError && (
                    <div className="text-xs text-red-600 border border-red-200 bg-red-50 rounded-lg px-3 py-2">{browseError}</div>
                  )}
                  {browse && !browseLoading && (
                    <BrowseResult result={browse} sort={sort} order={order} onSort={toggleSort} />
                  )}
                  {browse && !browseLoading && totalPages > 1 && (
                    <div className="flex items-center gap-2 mt-3 text-xs text-text-muted">
                      <button onClick={() => changePage(page - 1)} disabled={page <= 1}
                        className="p-1 border border-border-subtle rounded disabled:opacity-30 hover:bg-surface-elevated transition-colors">
                        <ChevronLeft size={13} />
                      </button>
                      <span>第 {page} / {totalPages} 页</span>
                      <button onClick={() => changePage(page + 1)} disabled={page >= totalPages}
                        className="p-1 border border-border-subtle rounded disabled:opacity-30 hover:bg-surface-elevated transition-colors">
                        <ChevronRight size={13} />
                      </button>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
