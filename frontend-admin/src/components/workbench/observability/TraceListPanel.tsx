'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { ChevronDown, Filter, RefreshCw, Scale, Search, Wrench } from 'lucide-react'
import { listTracesPage, listActiveTraces } from '@/api/observability'
import TraceBreadcrumb from '@/components/observability/trace/TraceBreadcrumb'
import { useToast } from '@/components/shared/Toast'
import { formatCost, formatRelative, formatTime, TRACE_SOURCE_LABELS, type TraceRecord, type TraceFilterOptions, type TraceStatus } from '@/types/trace'
import { getRunStatus, getRunStatusLabel, getRunStatusTone } from '@/lib/observability/runtimeStatus'
import { deriveDomainLabel, deriveRunKind, runKindBadgeClass } from '@/lib/observability/runKind'

/**
 * 执行记录列表（2026-10-30 管理端重构，按模拟页组件重写，不复用旧 TracesPanel/TraceRow/TraceFilter）。
 *
 * 不可消失机制全部重新实现：URL 深链同步、服务端分页/facets、越页回退、迟到响应丢弃、
 * 刷新保留旧数据 + 过期标记、空态/失败态区分、has_tool 筛选；运行中走授权活跃端点。
 * 确认砍掉且不迁移：列设置、收藏、前端 CSV 导出（后续由服务端全量导出替代）。
 */

const PAGE_SIZES = [20, 50, 100]
const TIME_RANGE_MS: Record<string, number> = {
  '15m': 15 * 60_000,
  '1h': 60 * 60_000,
  '6h': 6 * 60 * 60_000,
  '24h': 24 * 60 * 60_000,
}
const TIME_LABELS: Array<{ value: string; label: string }> = [
  { value: '15m', label: '近15分钟' },
  { value: '1h', label: '近1小时' },
  { value: '6h', label: '近6小时' },
  { value: '24h', label: '近24小时' },
  { value: 'custom', label: '自定义…' },
]
const STATUS_OPTIONS: Array<{ value: TraceStatus; label: string }> = [
  { value: 'all', label: '全部状态' },
  { value: 'success', label: '成功' },
  { value: 'failed', label: '业务失败' },
  { value: 'error', label: '执行错误' },
  { value: 'degraded', label: '降级' },
  { value: 'partial', label: '部分成功' },
  { value: 'timeout', label: '超时' },
  { value: 'cancelled', label: '取消' },
  { value: 'rejected', label: '拒绝' },
  { value: 'running', label: '运行中（实时）' },
  { value: 'unknown', label: '状态未知' },
]
const EMPTY_FILTER_OPTIONS: TraceFilterOptions = { workflow_names: [], models: [], kb_ids: [] }

interface ListFilter {
  timeRange: string
  since?: string
  until?: string
  status: TraceStatus
  keyword: string
  page: number
  pageSize: number
  source?: string
  workflow_name?: string
  kb_id?: string
  model?: string
  hasTool?: string
  rootOnly: boolean
  showQuestion: boolean
}

function readFilters(params: URLSearchParams): ListFilter {
  const rawStatus = params.get('status') as TraceStatus | null
  const rawRange = params.get('range') || '1h'
  const rawPageSize = Number(params.get('pageSize'))
  const since = params.get('since') || undefined
  const until = params.get('until') || undefined
  // 自定义区间必须带 since+until，否则按非法深链回退 1h
  const validCustom = rawRange === 'custom' && Boolean(since && until)
  return {
    timeRange: rawRange === 'custom' ? (validCustom ? 'custom' : '1h') : TIME_RANGE_MS[rawRange] ? rawRange : '1h',
    since: rawRange === 'custom' ? since : undefined,
    until: rawRange === 'custom' ? until : undefined,
    status: rawStatus || 'all',
    keyword: params.get('q') || '',
    page: Math.max(1, Number(params.get('page')) || 1),
    pageSize: PAGE_SIZES.includes(rawPageSize) ? rawPageSize : 20,
    source: params.get('source') || undefined,
    workflow_name: params.get('workflow') || undefined,
    kb_id: params.get('kb') || undefined,
    model: params.get('model') || undefined,
    hasTool: params.get('has_tool') || undefined,
    rootOnly: params.get('root') !== '0',
    showQuestion: params.get('showq') !== '0',
  }
}

function writeFilters(params: URLSearchParams, filter: ListFilter): URLSearchParams {
  params.set('tab', 'traces')
  params.set('range', filter.timeRange)
  if (filter.timeRange === 'custom') {
    if (filter.since) params.set('since', filter.since)
    if (filter.until) params.set('until', filter.until)
  } else {
    params.delete('since')
    params.delete('until')
  }
  params.set('status', filter.status)
  params.set('page', String(filter.page))
  params.set('pageSize', String(filter.pageSize))
  params.set('root', filter.rootOnly ? '1' : '0')
  params.set('showq', filter.showQuestion ? '1' : '0')
  const optional: Array<[string, string | undefined]> = [
    ['q', filter.keyword.trim() || undefined],
    ['source', filter.source],
    ['workflow', filter.workflow_name],
    ['kb', filter.kb_id],
    ['model', filter.model],
    ['has_tool', filter.hasTool],
  ]
  optional.forEach(([key, value]) => (value ? params.set(key, value) : params.delete(key)))
  return params
}

function csvSafeCell(value: unknown): string {
  return `"${String(value ?? '').replace(/"/g, '""')}"`
}

export default function TraceListPanel() {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const toast = useToast()

  const [filter, setFilter] = useState<ListFilter>(() => readFilters(new URLSearchParams(searchParams.toString())))
  const [traces, setTraces] = useState<TraceRecord[]>([])
  const [filterOptions, setFilterOptions] = useState<TraceFilterOptions>(EMPTY_FILTER_OPTIONS)
  const [totalTraces, setTotalTraces] = useState(0)
  const [retentionLimit, setRetentionLimit] = useState(5000)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [requestFailed, setRequestFailed] = useState(false)
  const [pageOutOfRange, setPageOutOfRange] = useState(false)
  const [lastFetchedAt, setLastFetchedAt] = useState<string | null>(null)
  const [pageError, setPageError] = useState('')
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [showMoreFilters, setShowMoreFilters] = useState(false)
  const [showCustomRange, setShowCustomRange] = useState(false)
  const [customSince, setCustomSince] = useState('')
  const [customUntil, setCustomUntil] = useState('')
  const [sortField, setSortField] = useState<'duration_ms' | 'timestamp' | ''>('')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [compareIds, setCompareIds] = useState<Set<string>>(new Set())
  const [copiedId, setCopiedId] = useState<string | null>(null)

  const loadedOnce = useRef(false)
  const requestSequence = useRef(0)
  const pageFallbackKey = useRef<string | null>(null)
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    const next = readFilters(new URLSearchParams(searchParams.toString()))
    setFilter((current) => (JSON.stringify(current) === JSON.stringify(next) ? current : next))
  }, [searchParams])

  const loadTraces = useCallback(async (mode: 'query' | 'refresh' = 'query') => {
    const requestId = ++requestSequence.current
    const queryKey = JSON.stringify({ ...filter, page: 1 })
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
    try {
      if (filter.status === 'running') {
        // 运行中走授权活跃端点（/traces/active）：内存实时记录，无分页与 facets
        const active = await listActiveTraces()
        if (requestId !== requestSequence.current) return
        setTraces(active)
        setTotalTraces(active.length)
        setFilterOptions(EMPTY_FILTER_OPTIONS)
        setLastFetchedAt(new Date().toISOString())
        setRequestFailed(false)
        loadedOnce.current = true
        return
      }
      const until = filter.timeRange === 'custom' && filter.until ? new Date(filter.until) : new Date()
      const rangeMs = TIME_RANGE_MS[filter.timeRange] ?? TIME_RANGE_MS['24h']
      const since = filter.timeRange === 'custom' && filter.since
        ? new Date(filter.since)
        : new Date(until.getTime() - rangeMs)
      const result = await listTracesPage({
        limit: filter.pageSize,
        offset: (filter.page - 1) * filter.pageSize,
        workflowName: filter.workflow_name,
        hasTool: filter.hasTool || undefined,
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
          const corrected = { ...filter, page: lastPage }
          setFilter(corrected)
          router.replace(`${pathname}?${writeFilters(new URLSearchParams(searchParams.toString()), corrected).toString()}`, { scroll: false })
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
  }, [filter, pathname, router, searchParams])

  useEffect(() => {
    void loadTraces('query')
    return () => { requestSequence.current += 1 }
  }, [loadTraces])

  // 自动刷新（30s）：仅用户显式开启；走 refresh 模式保留旧数据
  useEffect(() => {
    if (!autoRefresh) return
    const timer = setInterval(() => { void loadTraces('refresh') }, 30_000)
    return () => clearInterval(timer)
  }, [autoRefresh, loadTraces])

  const updateFilter = (next: ListFilter) => {
    const updated = { ...next, page: 1 }
    pageFallbackKey.current = null
    setFilter(updated)
    router.replace(`${pathname}?${writeFilters(new URLSearchParams(searchParams.toString()), updated).toString()}`, { scroll: false })
  }

  // 视图偏好（只看根 Trace / 显示问题摘要）：与排序同级，仅本地状态不进 URL
  const patchFilter = (patch: Partial<ListFilter>) => {
    pageFallbackKey.current = null
    setFilter((current) => ({ ...current, ...patch }))
  }

  const updatePage = (next: ListFilter) => {
    pageFallbackKey.current = null
    setFilter(next)
    router.replace(`${pathname}?${writeFilters(new URLSearchParams(searchParams.toString()), next).toString()}`, { scroll: false })
  }

  const applyCustomRange = () => {
    if (!customSince || !customUntil) {
      toast.warning('请选择起止时间')
      return
    }
    updateFilter({ ...filter, timeRange: 'custom', since: new Date(customSince).toISOString(), until: new Date(customUntil).toISOString() })
    setShowCustomRange(false)
  }

  const currentListUrl = useMemo(() => {
    const params = writeFilters(new URLSearchParams(searchParams.toString()), filter)
    return `${pathname}?${params.toString()}`
  }, [filter, pathname, searchParams])

  // 只看根 Trace：服务端暂无 parent 维度筛选，客户端过滤当前页（口径在页脚说明）
  const visibleBase = useMemo(() => {
    const base = filter.status === 'running'
      ? traces.filter((t) => getRunStatus(t) === 'running')
      : traces.filter((t) => getRunStatus(t) !== 'running')
    return filter.rootOnly ? base.filter((t) => !t.parent_id) : base
  }, [traces, filter.status, filter.rootOnly])

  const sorted = useMemo(() => {
    if (!sortField) return visibleBase
    return [...visibleBase].sort((a, b) => {
      const left = sortField === 'duration_ms' ? a.duration_ms : Date.parse(a.timestamp)
      const right = sortField === 'duration_ms' ? b.duration_ms : Date.parse(b.timestamp)
      return sortDir === 'desc' ? right - left : left - right
    })
  }, [visibleBase, sortDir, sortField])

  const total = totalTraces
  const totalPages = Math.max(1, Math.ceil(total / filter.pageSize))
  const safePage = Math.min(filter.page, totalPages)
  const maxDuration = useMemo(() => sorted.reduce((max, t) => Math.max(max, t.duration_ms || 0), 0), [sorted])

  const hasUnverifiedRunningRecords = useMemo(
    () => filter.status !== 'running' && traces.some((t) => getRunStatus(t) === 'running'),
    [traces, filter.status],
  )

  const handleNavigate = useCallback((traceId: string) => {
    const params = new URLSearchParams({ returnTo: currentListUrl })
    router.push(`/observability/traces/${encodeURIComponent(traceId)}?${params.toString()}`)
  }, [currentListUrl, router])

  const handleCopy = useCallback(async (id: string) => {
    try {
      await navigator.clipboard.writeText(id)
      setCopiedId(id)
      window.setTimeout(() => setCopiedId((c) => (c === id ? null : c)), 1500)
    } catch {
      toast.warning('无法访问剪贴板，请从详情页复制 Trace ID')
    }
  }, [toast])

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

  const handleSort = (field: 'duration_ms' | 'timestamp') => {
    setSortDir(sortField === field && sortDir === 'desc' ? 'asc' : 'desc')
    setSortField(field)
  }

  const onKeywordInput = (value: string) => {
    // 400ms 防抖后写 URL 并重新查询，避免逐键请求
    if (searchTimer.current) clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => {
      updateFilter({ ...filter, keyword: value })
    }, 400)
  }

  const sortButton = (label: string, field: 'duration_ms' | 'timestamp') => (
    <button type="button" onClick={() => handleSort(field)} className="rounded px-1 hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
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
          <p className="mt-1 text-sm text-text-secondary">按时间和终态筛选最近授权记录；失败运行的问题与错误摘要在详情页展示并审计。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link href="/observability/selection-funnel" className="inline-flex min-h-10 items-center rounded-md border border-border-subtle bg-white px-3 text-sm text-text-secondary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">选品记录</Link>
          <Link href="/observability/alerts" className="inline-flex min-h-10 items-center rounded-md border border-border-subtle bg-white px-3 text-sm text-text-secondary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">系统告警</Link>
          {compareIds.size >= 2 && (
            <button type="button" onClick={goCompare} className="inline-flex min-h-10 items-center gap-1.5 rounded-md bg-accent px-3 text-sm font-medium text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"><Scale size={14} />对比 {compareIds.size} 条</button>
          )}
          <button type="button" onClick={() => void loadTraces('refresh')} disabled={refreshing} className="inline-flex min-h-10 items-center gap-1.5 rounded-md border border-border-subtle bg-white px-3 text-sm font-medium text-text-primary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-60"><RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />{refreshing ? '刷新中…' : '刷新'}</button>
        </div>
      </div>

      <div className="rounded-md border border-border-subtle bg-white px-4 py-3 text-xs leading-5 text-text-secondary">
        <p>数据源：<code>GET /api/observability/traces</code>（运行中走 <code>GET /traces/active</code>）；服务端先执行身份、资源和租户授权，再按时间、状态及其他条件筛选并分页。</p>
        <p>总数来自 TraceStore 最近最多 {retentionLimit.toLocaleString()} 条留存记录；本页最多 {filter.pageSize} 条；「只看根 Trace」在当前页内过滤父子关系。查询时间：{lastFetchedAt ? new Date(lastFetchedAt).toLocaleString('zh-CN') : '尚未成功'}。</p>
      </div>

      {requestFailed && lastFetchedAt && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900" role="status">
          刷新失败，以下为上次成功读取于 {new Date(lastFetchedAt).toLocaleString('zh-CN')} 的记录，当前已标记为过期。{pageError && ` ${pageError}`}
        </div>
      )}

      {hasUnverifiedRunningRecords && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950" role="status">
          历史列表中存在未确认的 <code>running</code> 状态记录，已从列表排除；实时运行请用「运行中（实时）」筛选。
        </div>
      )}

      {filter.hasTool && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-900">
          <Wrench size={15} aria-hidden="true" />
          <span>仅显示匹配 Tool：<code className="font-mono">{filter.hasTool}</code>（服务端在已授权留存记录中筛选）</span>
          <button type="button" onClick={() => updateFilter({ ...filter, hasTool: undefined })} className="min-h-10 rounded px-2 font-medium underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">清除过滤</button>
        </div>
      )}

      {/* 单行筛选栏（模拟页形态） */}
      <div className="flex flex-wrap items-center gap-2.5">
        <div className="flex rounded-lg border border-border-subtle overflow-hidden bg-white">
          {TIME_LABELS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              aria-pressed={filter.timeRange === opt.value}
              onClick={() => {
                if (opt.value === 'custom') { setShowCustomRange((v) => !v); return }
                setShowCustomRange(false)
                updateFilter({ ...filter, timeRange: opt.value, since: undefined, until: undefined })
              }}
              className={`px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent ${filter.timeRange === opt.value ? 'bg-accent text-white' : 'text-text-secondary hover:bg-bg-hover'}`}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <select aria-label="运行状态" value={filter.status} onChange={(e) => updateFilter({ ...filter, status: e.target.value as TraceStatus })} className="min-h-10 rounded-lg border border-border-subtle bg-white px-3 py-1.5 text-xs text-text-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
          {STATUS_OPTIONS.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
        </select>
        <select aria-label="Trace 来源" value={filter.source ?? ''} onChange={(e) => updateFilter({ ...filter, source: e.target.value || undefined })} className="min-h-10 rounded-lg border border-border-subtle bg-white px-3 py-1.5 text-xs text-text-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
          <option value="">全部来源</option>
          {Object.entries(TRACE_SOURCE_LABELS).filter(([value]) => value).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <div className="relative min-w-[200px] flex-1">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
          <input
            aria-label="搜索问题 / Trace ID / 工作流"
            type="text"
            defaultValue={filter.keyword}
            onChange={(e) => onKeywordInput(e.target.value)}
            placeholder="搜索问题 / Trace ID / 工作流…"
            className="min-h-10 w-full rounded-lg border border-border-subtle pl-8 pr-3 text-xs text-text-primary placeholder:text-slate-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          />
        </div>
        <div className="relative">
          <button type="button" aria-expanded={showMoreFilters} onClick={() => setShowMoreFilters((v) => !v)} className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-dashed border-border-default bg-transparent px-3 text-xs text-text-secondary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
            <Filter size={13} aria-hidden="true" />更多筛选（Agent · KB · 模型 · Tool）<ChevronDown size={12} aria-hidden="true" />
          </button>
          {showMoreFilters && (
            <fieldset className="absolute right-0 z-10 mt-1 w-72 rounded-lg border border-border-subtle bg-white p-3 shadow-sm">
              <legend className="sr-only">更多筛选</legend>
              <label className="mb-1 block text-[11px] text-text-secondary">Agent / Workflow</label>
              <select aria-label="Agent 或 Workflow" value={filter.workflow_name ?? ''} onChange={(e) => updateFilter({ ...filter, workflow_name: e.target.value || undefined })} className="mb-2 min-h-9 w-full rounded border border-border-default px-2 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                <option value="">全部</option>
                {filterOptions.workflow_names.map((w) => <option key={w} value={w}>{w}</option>)}
              </select>
              <label className="mb-1 block text-[11px] text-text-secondary">知识库</label>
              <select aria-label="知识库" value={filter.kb_id ?? ''} onChange={(e) => updateFilter({ ...filter, kb_id: e.target.value || undefined })} className="mb-2 min-h-9 w-full rounded border border-border-default px-2 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                <option value="">全部</option>
                {filterOptions.kb_ids.map((k) => <option key={k} value={k}>{k}</option>)}
              </select>
              <label className="mb-1 block text-[11px] text-text-secondary">模型</label>
              <select aria-label="模型" value={filter.model ?? ''} onChange={(e) => updateFilter({ ...filter, model: e.target.value || undefined })} className="mb-2 min-h-9 w-full rounded border border-border-default px-2 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                <option value="">全部</option>
                {filterOptions.models.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
              <label className="mb-1 block text-[11px] text-text-secondary">Tool 名称</label>
              <input aria-label="Tool 名称筛选" defaultValue={filter.hasTool ?? ''} onBlur={(e) => updateFilter({ ...filter, hasTool: e.target.value.trim() || undefined })} placeholder="如 calculate_tool" className="min-h-9 w-full rounded border border-border-default px-2 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent" />
              <p className="mt-2 text-[10px] text-text-muted">选项来自当前授权留存记录的 facets（服务端提取）</p>
            </fieldset>
          )}
        </div>
        <span className="flex-1" />
        <label className="inline-flex min-h-10 items-center gap-1.5 text-xs text-text-secondary">
          <input type="checkbox" aria-label="只看根 Trace" checked={filter.rootOnly} onChange={(e) => patchFilter({ rootOnly: e.target.checked })} className="accent-accent" />
          只看根 Trace
        </label>
        <label className="inline-flex min-h-10 items-center gap-1.5 text-xs text-text-secondary">
          <input type="checkbox" aria-label="显示问题摘要" checked={filter.showQuestion} onChange={(e) => patchFilter({ showQuestion: e.target.checked })} className="accent-accent" />
          显示问题摘要
        </label>
        <label className="inline-flex min-h-10 items-center gap-1.5 text-xs text-text-secondary">
          <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} className="accent-accent" />
          自动刷新 30s
        </label>
      </div>

      {showCustomRange && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-border-subtle bg-white px-3 py-2 text-xs text-text-secondary">
          <span>自定义区间（本地时间）</span>
          <input type="datetime-local" aria-label="开始时间" value={customSince} onChange={(e) => setCustomSince(e.target.value)} className="min-h-9 rounded border border-border-default px-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent" />
          <span>至</span>
          <input type="datetime-local" aria-label="结束时间" value={customUntil} onChange={(e) => setCustomUntil(e.target.value)} className="min-h-9 rounded border border-border-default px-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent" />
          <button type="button" onClick={applyCustomRange} className="min-h-9 rounded-md bg-accent px-3 font-medium text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">应用</button>
          <span className="text-[10px] text-text-muted">跨出留存窗口的部分将无数据（总数仅覆盖最近留存记录）</span>
        </div>
      )}

      {compareIds.size >= 2 && (
        <div className="flex items-center gap-2 rounded-md border border-accent bg-violet-50 px-3 py-2 text-sm text-text-primary">
          <Scale size={14} aria-hidden="true" />
          <span>已选 {compareIds.size} 条</span>
          <button type="button" onClick={goCompare} className="rounded bg-accent px-2.5 py-1 text-xs font-medium text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">对比（上限 4 条）</button>
          <button type="button" onClick={() => setCompareIds(new Set())} className="text-xs text-text-secondary underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">清除选择</button>
        </div>
      )}

      {pageOutOfRange ? (
        <section className="rounded-md border border-amber-300 bg-amber-50 p-6" aria-live="polite">
          <h3 className="font-medium text-amber-950">当前页已无匹配记录</h3>
          <p className="mt-1 text-sm text-amber-900">记录总数在查询期间发生变化，已尝试一次回退；请返回上一页或刷新。</p>
          <button type="button" onClick={() => updatePage({ ...filter, page: Math.max(1, filter.page - 1) })} className="mt-3 min-h-10 rounded-md border border-amber-400 bg-white px-3 text-sm font-medium text-amber-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-600">返回上一页</button>
        </section>
      ) : sorted.length === 0 && total === 0 && !requestFailed ? (
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
              <caption className="sr-only">执行记录列表，包含状态、运行对象、耗时、Token 和时间</caption>
              <thead className="bg-bg-elevated text-xs text-text-secondary">
                <tr>
                  <th scope="col" className="w-8 px-3 py-2.5"><span className="sr-only">选择对比</span></th>
                  <th scope="col" className="px-3 py-2.5 font-medium">状态</th>
                  <th scope="col" className="px-3 py-2.5 font-medium">运行对象</th>
                  <th scope="col" className="px-3 py-2.5 text-right font-medium">{sortButton('耗时', 'duration_ms')}</th>
                  <th scope="col" className="px-3 py-2.5 text-right font-medium">Token / 费用</th>
                  <th scope="col" className="px-3 py-2.5 text-right font-medium">{sortButton('开始时间', 'timestamp')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {sorted.map((trace) => {
                  const status = getRunStatus(trace)
                  const kind = deriveRunKind(trace)
                  const domain = deriveDomainLabel(trace)
                  const hasError = status === 'error' || status === 'failed'
                  const errorCode = hasError && typeof trace.error?.code === 'string' && trace.error.code.trim() ? trace.error.code : null
                  const totalTokens = trace.usage?.total_tokens ?? 0
                  const isChild = Boolean(trace.parent_id)
                  const ratio = maxDuration > 0 ? Math.max(Math.min(trace.duration_ms / maxDuration, 1), 0.02) : 0
                  return (
                    <tr
                      key={trace.id}
                      data-trace-id={trace.id}
                      className={`group cursor-pointer transition-colors hover:bg-violet-50/60 ${isChild ? 'bg-slate-50/60' : ''} ${compareIds.has(trace.id) ? 'ring-1 ring-violet-300 ring-inset' : ''}`}
                      onClick={() => handleNavigate(trace.id)}
                      title="点击打开 Trace 详情"
                    >
                      <td className="px-3 py-2.5 align-top" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          className="accent-accent"
                          aria-label={`选择 ${trace.id} 加入对比`}
                          checked={compareIds.has(trace.id)}
                          onChange={() => toggleCompare(trace.id)}
                        />
                      </td>
                      <td className="px-3 py-2.5 align-top">
                        <span className={`inline-flex rounded border px-2 py-0.5 text-xs font-medium ${getRunStatusTone(status)}`}>{getRunStatusLabel(status)}</span>
                        {trace.sla?.breached && <span className="mt-1 block text-[9px] font-semibold text-red-500" title={`SLA ${trace.sla.threshold_ms}ms`}>SLA</span>}
                      </td>
                      <td className="px-3 py-2.5">
                        {filter.showQuestion && (
                          <p className="max-w-[380px] truncate text-[13px] text-text-primary" title={trace.question || undefined}>{trace.question || '未采集'}</p>
                        )}
                        <div className="mt-0.5 flex items-center gap-1.5 font-mono text-[10px] text-text-muted">
                          {isChild && <span className="shrink-0 bg-violet-50 px-1 text-violet-600" title={`子任务 · 父 ${trace.parent_id?.slice(0, 8)}`}>↳</span>}
                          {(trace.children_ids?.length ?? 0) > 0 && <span className="shrink-0 bg-violet-50 px-1 text-violet-600" title={`${trace.children_ids?.length} 个子任务`}>↑ {trace.children_ids?.length}</span>}
                          <button type="button" onClick={(e) => { e.stopPropagation(); void handleCopy(trace.id) }} aria-label={copiedId === trace.id ? 'Trace ID 已复制' : `复制 Trace ID ${trace.id}`} className={`rounded px-0.5 hover:text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${copiedId === trace.id ? 'text-emerald-600' : ''}`}>
                            {copiedId === trace.id ? '已复制' : trace.id.slice(0, 12)}
                          </button>
                          {errorCode && <span className="rounded bg-red-50 px-1.5 font-mono text-red-700" title={typeof trace.error?.message === 'string' ? trace.error.message : undefined}>{errorCode}</span>}
                          <span className={`rounded border px-1 text-[10px] not-italic ${runKindBadgeClass(kind.key)}`}>{kind.label}</span>
                          <span className="rounded border border-dashed border-border-default px-1 not-italic">{domain}</span>
                          {trace.workflow_name && <span className="truncate not-italic" title={trace.workflow_name}>{trace.workflow_name}</span>}
                          {trace.sla?.breached && <span className="font-semibold not-italic text-red-500">SLA 违反</span>}
                        </div>
                      </td>
                      <td className="px-3 py-2.5 text-right align-top">
                        <div className="flex items-center justify-end gap-2">
                          <span className={`font-mono text-xs font-semibold tabular-nums ${trace.duration_ms >= 8000 ? 'text-red-600' : trace.duration_ms >= 4000 ? 'text-amber-600' : 'text-text-secondary'}`} title={trace.duration_ms > 0 ? `${trace.duration_ms}ms` : '未结束'}>
                            {trace.duration_ms > 0 ? (trace.duration_ms >= 1000 ? `${(trace.duration_ms / 1000).toFixed(1)}s` : `${trace.duration_ms}ms`) : '—'}
                          </span>
                          <div className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-100">
                            <div className={`h-full rounded-full ${hasError ? 'bg-red-400' : trace.duration_ms >= 8000 ? 'bg-red-300' : trace.duration_ms >= 4000 ? 'bg-amber-300' : 'bg-emerald-300'}`} style={{ width: `${ratio * 100}%` }} />
                          </div>
                        </div>
                      </td>
                      <td className="px-3 py-2.5 text-right align-top font-mono text-xs tabular-nums text-slate-600">
                        {totalTokens > 0 ? totalTokens.toLocaleString() : <span className="text-slate-300">—</span>}
                        <span className="text-[9px] text-slate-300"> tok</span>
                        <div className="mt-0.5 text-[10px] text-emerald-600">
                          {typeof trace.cost_cny === 'number' ? formatCost(trace.cost_cny) : <span className="text-slate-400">未采集费用</span>}
                        </div>
                      </td>
                      <td className="px-3 py-2.5 text-right align-top">
                        <div className="text-[11px] text-slate-600">{formatRelative(trace.timestamp)}</div>
                        <div className="font-mono text-[9px] text-slate-400" title={formatTime(trace.timestamp)}>{formatTime(trace.timestamp)}</div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border-subtle bg-white px-4 py-3">
            <span className="text-sm text-text-secondary">{filter.status === 'running' ? `共 ${total} 条活跃记录（实时）` : `第 ${safePage} / ${totalPages} 页 · ${total} 条匹配记录`}</span>
            {filter.status !== 'running' && (
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex min-h-10 items-center gap-2 text-sm text-text-secondary">
                  每页
                  <select aria-label="每页条数" value={filter.pageSize} onChange={(e) => updatePage({ ...filter, page: 1, pageSize: Number(e.target.value) })} className="min-h-10 rounded border border-border-default bg-white px-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                    {PAGE_SIZES.map((size) => <option key={size} value={size}>{size}</option>)}
                  </select>
                </label>
                <button type="button" disabled={safePage <= 1} onClick={() => updatePage({ ...filter, page: safePage - 1 })} className="min-h-10 rounded border border-border-default px-3 text-sm disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">上一页</button>
                <button type="button" disabled={safePage >= totalPages} onClick={() => updatePage({ ...filter, page: safePage + 1 })} className="min-h-10 rounded border border-border-default px-3 text-sm disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">下一页</button>
              </div>
            )}
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
