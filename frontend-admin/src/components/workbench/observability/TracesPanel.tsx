'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { Download, ShoppingBag, BellRing, Scale, Wrench } from 'lucide-react'
import { listTracesPage } from '@/api/observability'
import TraceFilterBar from '@/components/observability/trace/TraceFilter'
import TraceBreadcrumb from '@/components/observability/trace/TraceBreadcrumb'
import TraceRow, { ColKey, VALID_COL_KEYS } from '@/components/observability/trace/TraceRow'
import { useToast } from '@/components/shared/Toast'
import { TraceFilter, TraceFilterOptions, TraceRecord, TraceStatus } from '@/types/trace'
import { getRunStatus } from '@/lib/observability/runtimeStatus'

const PAGE_SIZES = [20, 50, 100]
const TIME_RANGE_MS: Record<Exclude<TraceFilter['timeRange'], 'custom'>, number> = {
  '15m': 15 * 60_000,
  '1h': 60 * 60_000,
  '6h': 6 * 60 * 60_000,
  '24h': 24 * 60 * 60_000,
}
const BOOKMARK_KEY = 'obs.bookmarks'
const COLUMNS_KEY = 'obs.traceColumns'
const EMPTY_FILTER_OPTIONS: TraceFilterOptions = { workflow_names: [], models: [], kb_ids: [] }
const DEFAULT_COLUMNS: ColKey[] = ['status', 'question', 'duration', 'usage', 'time', 'actions']
const ALL_COLUMNS: { key: ColKey; label: string }[] = [
  { key: 'status', label: '状态' },
  { key: 'question', label: '运行对象 / Trace ID' },
  { key: 'duration', label: '耗时' },
  { key: 'usage', label: 'Token / 费用' },
  { key: 'time', label: '时间' },
  { key: 'actions', label: '操作' },
]
const VALID_STATUSES = new Set<TraceStatus>([
  'all', 'success', 'failed', 'error', 'degraded', 'partial', 'timeout',
  'cancelled', 'rejected', 'running', 'unknown',
])
const VALID_RANGES = new Set<TraceFilter['timeRange']>(['15m', '1h', '6h', '24h', 'custom'])

function readFilters(params: URLSearchParams): TraceFilter {
  const rawStatus = params.get('status') as TraceStatus | null
  const rawRange = params.get('range') as TraceFilter['timeRange'] | null
  const rawPageSize = Number(params.get('pageSize'))
  return {
    timeRange: rawRange && VALID_RANGES.has(rawRange) ? rawRange : '1h',
    status: rawStatus && VALID_STATUSES.has(rawStatus) ? rawStatus : 'all',
    keyword: params.get('q') || '',
    sortField: '',
    sortDir: 'desc',
    page: Math.max(1, Number(params.get('page')) || 1),
    pageSize: PAGE_SIZES.includes(rawPageSize) ? rawPageSize : 20,
    source: params.get('source') || undefined,
    workflow_name: params.get('workflow') || undefined,
    kb_id: params.get('kb') || undefined,
    model: params.get('model') || undefined,
  }
}

function writeFilters(params: URLSearchParams, filter: TraceFilter): URLSearchParams {
  params.set('tab', 'traces')
  params.set('range', filter.timeRange)
  params.set('status', filter.status)
  params.set('page', String(filter.page))
  params.set('pageSize', String(filter.pageSize))
  const optional: Array<[string, string | undefined]> = [
    ['q', filter.keyword.trim() || undefined],
    ['source', filter.source],
    ['workflow', filter.workflow_name],
    ['kb', filter.kb_id],
    ['model', filter.model],
  ]
  optional.forEach(([key, value]) => value ? params.set(key, value) : params.delete(key))
  return params
}

function csvCell(value: unknown): string {
  const text = String(value ?? '')
  return `"${text.replace(/"/g, '""')}"`
}

export default function TracesPanel() {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const toast = useToast()
  const [filter, setFilter] = useState<TraceFilter>(() => readFilters(new URLSearchParams(searchParams.toString())))
  const [hasToolFilter, setHasToolFilter] = useState(searchParams.get('has_tool') || '')
  const [traces, setTraces] = useState<TraceRecord[]>([])
  const [filterOptions, setFilterOptions] = useState<TraceFilterOptions>(EMPTY_FILTER_OPTIONS)
  const [totalTraces, setTotalTraces] = useState(0)
  const [retentionLimit, setRetentionLimit] = useState(5000)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [requestFailed, setRequestFailed] = useState(false)
  const [pageOutOfRange, setPageOutOfRange] = useState(false)
  const [lastFetchedAt, setLastFetchedAt] = useState<string | null>(null)
  const loadedOnce = useRef(false)
  const requestSequence = useRef(0)
  const pageFallbackKey = useRef<string | null>(null)
  const [pageError, setPageError] = useState('')
  const [sortField, setSortField] = useState<'duration_ms' | 'timestamp' | 'cost_usd' | ''>('')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [bookmarks, setBookmarks] = useState<Set<string>>(new Set())
  const [columns, setColumns] = useState<ColKey[]>(DEFAULT_COLUMNS)
  const [showColumns, setShowColumns] = useState(false)
  const [compareIds, setCompareIds] = useState<Set<string>>(new Set())
  const [copiedId, setCopiedId] = useState<string | null>(null)

  useEffect(() => {
    const params = new URLSearchParams(searchParams.toString())
    const nextFilter = readFilters(params)
    setFilter((current) => JSON.stringify(current) === JSON.stringify(nextFilter) ? current : nextFilter)
    const nextToolFilter = params.get('has_tool') || ''
    setHasToolFilter((current) => current === nextToolFilter ? current : nextToolFilter)
  }, [searchParams])

  useEffect(() => {
    try {
      const savedBookmarks = localStorage.getItem(BOOKMARK_KEY)
      if (savedBookmarks) setBookmarks(new Set(JSON.parse(savedBookmarks)))
      const savedColumns = localStorage.getItem(COLUMNS_KEY)
      if (savedColumns) {
        const valid = (JSON.parse(savedColumns) as ColKey[]).filter((key) => VALID_COL_KEYS.includes(key))
        setColumns(valid.length > 0 ? valid : DEFAULT_COLUMNS)
      }
    } catch {
      setBookmarks(new Set())
      setColumns(DEFAULT_COLUMNS)
    }
  }, [])

  useEffect(() => { localStorage.setItem(BOOKMARK_KEY, JSON.stringify([...bookmarks])) }, [bookmarks])
  useEffect(() => { localStorage.setItem(COLUMNS_KEY, JSON.stringify(columns)) }, [columns])

  const loadTraces = useCallback(async (mode: 'query' | 'refresh' = 'query') => {
    const requestId = ++requestSequence.current
    const queryKey = JSON.stringify({ ...filter, page: 1, hasToolFilter })
    if (mode === 'refresh') pageFallbackKey.current = null
    const preserveData = mode === 'refresh' && loadedOnce.current
    if (preserveData) setRefreshing(true)
    else {
      setLoading(true)
      setTraces([])
      setFilterOptions(EMPTY_FILTER_OPTIONS)
      setLastFetchedAt(null)
      setPageError('')
    }
    setRequestFailed(false)
    setPageOutOfRange(false)
    if (filter.status === 'running') {
      setTraces([])
      setFilterOptions(EMPTY_FILTER_OPTIONS)
      setTotalTraces(0)
      setLastFetchedAt(null)
      setLoading(false)
      setRefreshing(false)
      return
    }
    try {
      const until = new Date()
      const timeRange = filter.timeRange === 'custom' ? '24h' : filter.timeRange
      const since = new Date(until.getTime() - TIME_RANGE_MS[timeRange])
      const result = await listTracesPage({
        limit: filter.pageSize,
        offset: (filter.page - 1) * filter.pageSize,
        workflowName: filter.workflow_name,
        hasTool: hasToolFilter || undefined,
        source: filter.source,
        status: filter.status,
        since: since.toISOString(),
        until: until.toISOString(),
        excludeRunning: true,
        model: filter.model,
        kbId: filter.kb_id,
        query: filter.keyword,
      })
      if (requestId !== requestSequence.current) return
      setFilterOptions(result.facets ?? EMPTY_FILTER_OPTIONS)
      const lastPage = Math.max(1, Math.ceil(result.total / filter.pageSize))
      if (filter.page > lastPage) {
        if (pageFallbackKey.current !== queryKey) {
          pageFallbackKey.current = queryKey
          const correctedFilter = { ...filter, page: lastPage }
          setFilter(correctedFilter)
          const params = writeFilters(new URLSearchParams(searchParams.toString()), correctedFilter)
          router.replace(`${pathname}?${params.toString()}`, { scroll: false })
        } else {
          setTraces([])
          setTotalTraces(result.total)
          setRetentionLimit(result.retention_limit)
          setLastFetchedAt(new Date().toISOString())
          setPageOutOfRange(true)
        }
        return
      }
      setTraces(result.traces)
      setTotalTraces(result.total)
      setRetentionLimit(result.retention_limit)
      setLastFetchedAt(new Date().toISOString())
      setRequestFailed(false)
      loadedOnce.current = true
    } catch (error) {
      if (requestId !== requestSequence.current) return
      setRequestFailed(true)
      setPageError(errorMessage(error))
    } finally {
      if (requestId === requestSequence.current) {
        setLoading(false)
        setRefreshing(false)
      }
    }
  }, [filter, hasToolFilter, pathname, router, searchParams])

  useEffect(() => {
    void loadTraces('query')
    return () => { requestSequence.current += 1 }
  }, [loadTraces])

  const updateFilter = (next: TraceFilter) => {
    const updated = { ...next, page: 1 }
    pageFallbackKey.current = null
    setFilter(updated)
    const params = writeFilters(new URLSearchParams(searchParams.toString()), updated)
    router.replace(`${pathname}?${params.toString()}`, { scroll: false })
  }

  const updatePage = (next: TraceFilter) => {
    pageFallbackKey.current = null
    setFilter(next)
    const params = writeFilters(new URLSearchParams(searchParams.toString()), next)
    router.replace(`${pathname}?${params.toString()}`, { scroll: false })
  }

  const currentListUrl = useMemo(() => {
    const params = writeFilters(new URLSearchParams(searchParams.toString()), filter)
    if (hasToolFilter) params.set('has_tool', hasToolFilter)
    return `${pathname}?${params.toString()}`
  }, [filter, hasToolFilter, pathname, searchParams])

  const filteredBase = useMemo(
    // 时间、状态、来源、Agent/Workflow 和检索条件均已由服务端应用；排序仅作用于当前页。
    () => traces.filter((trace) => getRunStatus(trace) !== 'running'),
    [traces],
  )

  const hasUnverifiedRunningRecords = useMemo(
    () => traces.some((trace) => getRunStatus(trace) === 'running'),
    [traces],
  )

  const filtered = useMemo(() => {
    if (!sortField) return filteredBase
    return [...filteredBase].sort((a, b) => {
      const left = sortField === 'duration_ms' ? a.duration_ms
        : sortField === 'timestamp' ? Date.parse(a.timestamp)
          : (a.cost_cny ?? -1)
      const right = sortField === 'duration_ms' ? b.duration_ms
        : sortField === 'timestamp' ? Date.parse(b.timestamp)
          : (b.cost_cny ?? -1)
      return sortDir === 'desc' ? right - left : left - right
    })
  }, [filteredBase, sortDir, sortField])

  const total = totalTraces
  const totalPages = Math.max(1, Math.ceil(total / filter.pageSize))
  const safePage = Math.min(filter.page, totalPages)
  const pageTraces = filtered
  const maxDuration = useMemo(() => filtered.reduce((max, trace) => Math.max(max, trace.duration_ms || 0), 0), [filtered])

  const handleSort = (field: 'duration_ms' | 'timestamp' | 'cost_usd') => {
    setSortDir(sortField === field && sortDir === 'desc' ? 'asc' : 'desc')
    setSortField(field)
  }

  const handleNavigate = useCallback((target: string) => {
    const detailId = target.startsWith('/') ? target.split('/').filter(Boolean).pop() || '' : target
    const params = new URLSearchParams({ returnTo: currentListUrl })
    router.push(`/observability/traces/${encodeURIComponent(detailId)}?${params.toString()}`)
  }, [currentListUrl, router])

  const handleCopy = useCallback(async (id: string) => {
    try {
      await navigator.clipboard.writeText(id)
      setCopiedId(id)
      window.setTimeout(() => setCopiedId((current) => current === id ? null : current), 1500)
    } catch {
      toast.warning('无法访问剪贴板，请从详情页复制 Trace ID')
    }
  }, [toast])

  const toggleBookmark = useCallback((id: string) => {
    setBookmarks((current) => {
      const next = new Set(current)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }, [])

  const toggleCompare = useCallback((id: string) => {
    setCompareIds((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else if (next.size < 4) next.add(id)
      else {
        toast.warning('最多对比 4 条 Trace')
        return current
      }
      return next
    })
  }, [toast])

  const goCompare = () => {
    const params = new URLSearchParams({ ids: [...compareIds].join(','), returnTo: currentListUrl })
    router.push(`/observability/traces/compare?${params.toString()}`)
  }

  const exportCsv = () => {
    // error_code 是分类法枚举值（非自由文本），属安全字段；错误消息原文仅详情页展示
    const headers = ['trace_id', 'workflow_name', 'source', 'status', 'duration_ms', 'timestamp', 'cost_cny', 'error_code']
    const rows = filtered.map((trace) => [
      trace.id,
      trace.workflow_name || '',
      trace.source || '',
      getRunStatus(trace),
      trace.duration_ms,
      trace.timestamp,
      typeof trace.cost_cny === 'number' ? trace.cost_cny : '',
      typeof trace.error?.code === 'string' ? trace.error.code : '',
    ].map(csvCell).join(','))
    const blob = new Blob([[headers.map(csvCell).join(','), ...rows].join('\n')], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `runtime-runs-${new Date().toISOString().slice(0, 10)}.csv`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  const clearToolFilter = () => {
    const params = new URLSearchParams(searchParams.toString())
    params.delete('has_tool')
    router.replace(`${pathname}?${params.toString()}`, { scroll: false })
    setHasToolFilter('')
  }

  const sortButton = (label: string, field: 'duration_ms' | 'timestamp' | 'cost_usd') => (
    <button type="button" onClick={() => handleSort(field)} className="min-h-10 rounded px-1 text-left hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
      {label}{sortField === field ? (sortDir === 'desc' ? ' ↓' : ' ↑') : ' ↕'}
    </button>
  )

  if (loading && !lastFetchedAt) {
    return <div className="p-6 text-sm text-text-secondary" role="status">正在读取执行记录…</div>
  }

  if (requestFailed && !lastFetchedAt) {
    return (
      <section className="m-4 rounded-lg border border-red-200 bg-red-50 p-4 sm:m-6" role="alert">
        <h2 className="font-semibold text-red-800">执行记录暂不可用</h2>
        <p className="mt-1 text-sm text-red-700">{pageError || 'Trace 服务读取失败。'} 没有用空列表或模拟数据替代。</p>
        <button type="button" onClick={() => void loadTraces('query')} className="mt-3 min-h-10 rounded-md border border-red-300 bg-white px-3 text-sm font-medium text-red-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500">重试读取</button>
      </section>
    )
  }

  return (
    <div className="space-y-4 p-4 sm:p-6">
      <TraceBreadcrumb crumbs={[{ label: '运行控制中心', href: '/observability/monitoring?tab=overview' }, { label: '执行记录' }]} />

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-text-primary">执行记录</h2>
          <p className="mt-1 text-sm text-text-secondary">按时间和终态筛选最近授权记录；输入与响应内容、Session 标识不会出现在列表。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link href="/observability/selection-funnel" className="inline-flex min-h-10 items-center gap-1.5 rounded-md border border-border-subtle bg-white px-3 text-sm text-text-secondary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"><ShoppingBag size={14} />选品记录</Link>
          <Link href="/observability/alerts" className="inline-flex min-h-10 items-center gap-1.5 rounded-md border border-border-subtle bg-white px-3 text-sm text-text-secondary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"><BellRing size={14} />系统告警</Link>
          {compareIds.size >= 2 && <button type="button" onClick={goCompare} className="min-h-10 rounded-md bg-accent px-3 text-sm font-medium text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">对比 {compareIds.size} 条</button>}
          <button type="button" onClick={exportCsv} className="inline-flex min-h-10 items-center gap-1.5 rounded-md border border-border-subtle bg-white px-3 text-sm text-text-secondary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"><Download size={14} />导出当前页安全字段</button>
          <button type="button" onClick={() => void loadTraces('refresh')} disabled={refreshing} className="min-h-10 rounded-md border border-border-subtle bg-white px-3 text-sm font-medium text-text-primary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-60">{refreshing ? '刷新中…' : '刷新'}</button>
        </div>
      </div>

      <div className="rounded-md border border-border-subtle bg-white px-4 py-3 text-xs leading-5 text-text-secondary">
        <p>数据源：<code>GET /api/observability/traces</code>；服务端先执行身份、资源和租户授权，再按时间、状态及其他条件筛选并分页。</p>
        <p>总数来自 TraceStore 最近最多 {retentionLimit.toLocaleString()} 条留存记录；本页最多 {filter.pageSize} 条。运行总览仍最多展示 200 条样本。查询时间：{lastFetchedAt ? new Date(lastFetchedAt).toLocaleString('zh-CN') : '尚未成功'}。</p>
      </div>

      {requestFailed && lastFetchedAt && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900" role="status">
          刷新失败，以下为上次成功读取于 {new Date(lastFetchedAt).toLocaleString('zh-CN')} 的记录，当前已标记为过期。{pageError && ` ${pageError}`}
        </div>
      )}

      {hasUnverifiedRunningRecords && filter.status !== 'running' && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950" role="status">
          历史列表中存在未确认的 <code>running</code> 状态记录，已从列表与导出中排除；它们不代表当前活跃执行。
        </div>
      )}

      {hasToolFilter && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-900">
          <Wrench size={15} aria-hidden="true" />
          <span>仅显示匹配 Tool：<code className="font-mono">{hasToolFilter}</code>（服务端在已授权留存记录中筛选）</span>
          <button type="button" onClick={clearToolFilter} className="min-h-10 rounded px-2 font-medium underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">清除过滤</button>
        </div>
      )}

      <div className="rounded-md border border-border-subtle bg-white p-3">
        <TraceFilterBar filter={filter} onChange={updateFilter} options={filterOptions} />
        <div className="relative mt-3 flex flex-wrap items-center gap-3 border-t border-border-subtle pt-2">
          <button type="button" onClick={() => setShowColumns((value) => !value)} aria-expanded={showColumns} className="min-h-10 rounded px-2 text-sm text-text-secondary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">列设置（{columns.length}/{ALL_COLUMNS.length}）</button>
          {showColumns && (
            <fieldset className="flex flex-wrap gap-2 rounded-md border border-border-subtle bg-white p-2">
              <legend className="sr-only">显示列</legend>
              {ALL_COLUMNS.map((column) => (
                <label key={column.key} className="inline-flex min-h-10 items-center gap-2 px-2 text-sm text-text-secondary">
                  <input type="checkbox" checked={columns.includes(column.key)} onChange={() => setColumns((current) => current.includes(column.key) ? current.filter((key) => key !== column.key) : [...current, column.key])} />
                  {column.label}
                </label>
              ))}
            </fieldset>
          )}
          <span className="text-sm text-text-secondary" aria-live="polite">当前筛选 {filter.status === 'running' ? '暂不可统计' : `${total} 条`} / 当前页 {traces.length} 条</span>
        </div>
      </div>

      {filter.status === 'running' ? (
        <section className="rounded-md border border-amber-300 bg-amber-50 p-6" role="status">
          <h3 className="font-semibold text-amber-950">运行中记录暂不可安全查询</h3>
          <p className="mt-1 text-sm text-amber-900">当前活跃 Trace 接口缺少资源级授权。为避免越权读取或把历史列表当作实时数据，此筛选标记为 BLOCKED，也不显示伪造的空结果。</p>
          <button type="button" onClick={() => updateFilter({ ...filter, status: 'all' })} className="mt-3 min-h-10 rounded-md border border-amber-400 bg-white px-3 text-sm font-medium text-amber-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-600">清除运行中筛选</button>
        </section>
      ) : pageOutOfRange ? (
        <section className="rounded-md border border-amber-300 bg-amber-50 p-6" aria-live="polite">
          <h3 className="font-medium text-amber-950">当前页已无匹配记录</h3>
          <p className="mt-1 text-sm text-amber-900">记录总数在查询期间发生变化，已尝试一次回退；请返回上一页或刷新。</p>
          <button type="button" onClick={() => updatePage({ ...filter, page: Math.max(1, filter.page - 1) })} className="mt-3 min-h-10 rounded-md border border-amber-400 bg-white px-3 text-sm font-medium text-amber-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-600">返回上一页</button>
        </section>
      ) : traces.length === 0 && total === 0 && !requestFailed ? (
        <section className="rounded-md border border-border-subtle bg-white p-6" aria-live="polite">
          <h3 className="font-medium text-text-primary">当前筛选条件下无匹配记录</h3>
          <p className="mt-1 text-sm text-text-secondary">Trace API 成功返回 0 条；这不是服务错误。</p>
        </section>
      ) : total === 0 ? (
        <section className="rounded-md border border-border-subtle bg-white p-6" aria-live="polite">
          <h3 className="font-medium text-text-primary">当前筛选条件下无匹配记录</h3>
          <p className="mt-1 text-sm text-text-secondary">筛选覆盖 TraceStore 最近最多 {retentionLimit.toLocaleString()} 条留存记录中的已授权范围；可调整时间、状态或来源。</p>
        </section>
      ) : (
        <>
          <div className="overflow-x-auto rounded-md border border-border-subtle bg-white">
            <table className="w-full min-w-[760px] border-collapse text-left">
              <caption className="sr-only">执行记录列表，包含状态、工作流、耗时、Token 和时间</caption>
              <thead className="bg-bg-elevated text-xs text-text-secondary">
                <tr>
                  {columns.includes('status') && <th scope="col" className="px-3 py-2.5 font-medium">状态</th>}
                  {columns.includes('question') && <th scope="col" className="px-3 py-2.5 font-medium">运行对象 / Trace ID</th>}
                  {columns.includes('duration') && <th scope="col" className="px-3 py-2.5 text-right font-medium">{sortButton('耗时', 'duration_ms')}</th>}
                  {columns.includes('usage') && <th scope="col" className="px-3 py-2.5 text-right font-medium">Token / CNY</th>}
                  {columns.includes('time') && <th scope="col" className="px-3 py-2.5 font-medium">{sortButton('开始时间', 'timestamp')}</th>}
                  {columns.includes('actions') && <th scope="col" className="px-3 py-2.5 font-medium">操作</th>}
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {pageTraces.map((trace) => (
                  <TraceRow
                    key={trace.id}
                    t={trace}
                    columns={columns}
                    maxDuration={maxDuration}
                    isBookmarked={bookmarks.has(trace.id)}
                    isCompared={compareIds.has(trace.id)}
                    isCopied={copiedId === trace.id}
                    onNavigate={handleNavigate}
                    onCopy={handleCopy}
                    onToggleBookmark={toggleBookmark}
                    onToggleCompare={toggleCompare}
                  />
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border-subtle bg-white px-4 py-3">
            <span className="text-sm text-text-secondary">第 {safePage} / {totalPages} 页 · {total} 条匹配记录（当前页排序与导出）</span>
            <div className="flex flex-wrap items-center gap-2">
              <label className="flex min-h-10 items-center gap-2 text-sm text-text-secondary">
                每页
                <select aria-label="每页条数" value={filter.pageSize} onChange={(event) => updatePage({ ...filter, page: 1, pageSize: Number(event.target.value) })} className="min-h-10 rounded border border-border-default bg-white px-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                  {PAGE_SIZES.map((size) => <option key={size} value={size}>{size}</option>)}
                </select>
              </label>
              <button type="button" disabled={safePage <= 1} onClick={() => updatePage({ ...filter, page: safePage - 1 })} className="min-h-10 rounded border border-border-default px-3 text-sm disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">上一页</button>
              <button type="button" disabled={safePage >= totalPages} onClick={() => updatePage({ ...filter, page: safePage + 1 })} className="min-h-10 rounded border border-border-default px-3 text-sm disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">下一页</button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function errorMessage(error: unknown): string {
  const status = (error as { status?: number } | null)?.status
  if (status === 401) return '登录状态已失效，请重新登录后再试。'
  if (status === 403) return '当前账号无权读取这些运行记录。'
  if (status === 429) return '请求过于频繁，请稍后重试。'
  if (status && status >= 500) return 'Trace 服务暂不可用，请稍后重试。'
  return 'Trace 服务读取失败，请检查连接后重试。'
}
