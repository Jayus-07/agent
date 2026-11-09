'use client'

/**
 * /tools — Tool 治理（V2 治理工作台：动态工具的审核/授权/调试统一入口）
 *
 * 三个 subTab（状态进 URL query）：
 * - registry 工具目录与操作：静态契约 lock（getToolInventory）+ 动态 MCP/HTTP
 *   工具（getMcpTools）统一三来源目录；动态工具行打开详情抽屉（概览/Schema/
 *   执行策略/AI 语义/调试/历史），审核、业务域、授权与测试调用都在抽屉内完成。
 * - quality 运行质量与失败：运行统计、强制失败测试（runToolFailureProbe，
 *   只写模拟 Trace）、AI 失败分析（聚类归因/建议/候选评测用例，仅建议不
 *   生效）、失败 Tool 与七分类分布。
 * - contract 契约与变更历史：契约清单（数据源/额度/参数）+ 变更历史。
 *
 * 事实源：代码 + tool_contracts.lock.json（静态）/ ai.mcp_tools（动态，DB）。
 * 清单筛选（搜索/数据源/仅失败）与变更历史分类筛选进 URL querystring，
 * 分类过滤走服务端（changes 本地只有最近一页，前端过滤会漏更早记录）。
 *
 * 结构：本文件只保留页面数据编排与 tab 装配；静态契约详情（static-detail）、
 * 来源徽章（badges）、动态工具抽屉及其 section（ToolDrawer/ReviewPolicyForm/
 * DebugInvokeForm/SemanticContractSection/ToolHistorySection）、AI 失败分析
 * （FailureAnalysisSection）、共享常量（meta）各自独立文件。
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import {
  AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, RefreshCw, Search,
  ShieldCheck,
} from 'lucide-react'
import WorkbenchShell, { type WorkbenchTab } from '@/components/workbench/WorkbenchShell'
import { useWorkbenchTab } from '@/components/workbench/useWorkbenchTab'
import StatusChip, { type StatusTone } from '@/components/layout/StatusChip'
import StatMetricBar from '@/components/layout/StatMetricBar'
import { AssetActionButton, AssetSection, AssetStatCard } from '@/components/layout/AssetPageShell'
import ErrorState from '@/components/shared/ErrorState'
import EmptyState from '@/components/shared/EmptyState'
import Skeleton from '@/components/shared/Skeleton'
import {
  getToolContractChanges,
  getToolInventory,
  getToolStats,
  runToolFailureProbe,
  type ToolContractChange,
  type ToolFailureClass,
  type ToolInventory,
  type ToolStats,
} from '@/api/governance'
import { getMcpTools, type McpToolSummary } from '@/api/mcp'
import { DataSourceBadge, DynamicSourceBadge } from './badges'
import { StaticToolDetail, ToolRow } from './static-detail'
import { ToolDrawer } from './ToolDrawer'
import { FailureAnalysisSection } from './FailureAnalysisSection'
import { domainLabel, ERROR_CLASS_LABEL, fmtCount, MCP_STATUS_META } from './meta'

const TOOL_PAGE_TABS = [
  { id: 'registry', label: '工具目录与操作' },
  { id: 'quality', label: '运行质量与失败' },
  { id: 'contract', label: '契约与变更历史' },
] as const satisfies readonly WorkbenchTab<'registry' | 'quality' | 'contract'>[]

const TOOL_PAGE_TAB_IDS = TOOL_PAGE_TABS.map((tab) => tab.id) as readonly (
  | 'registry' | 'quality' | 'contract')[]

const CHANGE_KIND_LABEL: Record<string, string> = {
  tool_removed: '删除 Tool',
  tool_added: '新增 Tool',
  param_removed: '删除参数',
  param_added: '新增参数',
  param_type_changed: '参数类型变更',
  required_changed: '必填性变更',
  default_changed: '默认值变更',
  output_type_changed: '输出类型变更',
  capability_binding_changed: 'Capability 归属变更',
  description_changed: '描述变更',
}

const CLASS_META: Record<string, { label: string; cls: string }> = {
  BREAKING: { label: 'BREAKING', cls: 'bg-red-100 text-red-700' },
  DEGRADED: { label: 'DEGRADED', cls: 'bg-amber-100 text-amber-700' },
  COMPATIBLE: { label: 'COMPATIBLE', cls: 'bg-emerald-100 text-emerald-700' },
  INIT: { label: 'INIT', cls: 'bg-slate-100 text-slate-600' },
}

const FAILURE_PROBE_OPTIONS: { value: ToolFailureClass; label: string }[] = [
  { value: 'timeout', label: '超时' },
  { value: 'network_error', label: '网络不可用' },
  { value: 'permission_denied', label: '权限拒绝' },
  { value: 'validation_error', label: '参数校验' },
  { value: 'business_error', label: '业务失败' },
  { value: 'contract_error', label: '契约降级' },
  { value: 'provider_error', label: '供应商限流' },
]

/** 清单筛选：与 DataSourceBadge 同口径（无徽章 = internal） */
const DS_FILTER_OPTIONS: { value: string; label: string }[] = [
  { value: 'all', label: '全部数据源' },
  { value: 'mcp', label: 'MCP' },
  { value: 'rest', label: '外部 API' },
  { value: 'internal', label: '平台内部' },
]

const CLS_FILTER_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: '全部分类' },
  { value: 'BREAKING', label: 'BREAKING' },
  { value: 'DEGRADED', label: 'DEGRADED' },
  { value: 'COMPATIBLE', label: 'COMPATIBLE' },
  { value: 'INIT', label: 'INIT' },
]

type ChangeCls = '' | 'BREAKING' | 'DEGRADED' | 'COMPATIBLE' | 'INIT'

/** URL 读入的分类值只接受白名单，脏值回落全部分类 */
function parseCls(value: string | null): ChangeCls {
  const v = value ?? ''
  return (CLS_FILTER_OPTIONS.some((o) => o.value === v) ? v : '') as ChangeCls
}

export default function ToolsPage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { tab, selectTab } = useWorkbenchTab(TOOL_PAGE_TAB_IDS, 'registry')
  const [inventory, setInventory] = useState<ToolInventory | null>(null)
  const [stats, setStats] = useState<ToolStats | null>(null)
  const [changes, setChanges] = useState<ToolContractChange[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [probeTool, setProbeTool] = useState('')
  const [probeClass, setProbeClass] = useState<ToolFailureClass>('timeout')
  const [probeLoading, setProbeLoading] = useState(false)
  const [probeError, setProbeError] = useState<string | null>(null)
  const [probeResult, setProbeResult] = useState<{ trace_id: string; status: string } | null>(null)
  // 清单筛选（q/ds/failed）与变更历史分类（cls）进 URL，复用执行记录列表的 querystring 模式
  const [query, setQuery] = useState(searchParams.get('q') ?? '')
  const [dsFilter, setDsFilter] = useState(searchParams.get('ds') ?? 'all')
  const [failedOnly, setFailedOnly] = useState(searchParams.get('failed') === '1')
  const [clsFilter, setClsFilter] = useState<ChangeCls>(parseCls(searchParams.get('cls')))
  // 外部 MCP 动态工具（数据库事实源，非契约 lock；连接管理在 /mcp 连接中心）
  const [mcpTools, setMcpTools] = useState<McpToolSummary[]>([])
  const [mcpError, setMcpError] = useState<string | null>(null)
  // 动态工具详情抽屉
  const [drawerToolId, setDrawerToolId] = useState<number | null>(null)
  // registry 目录静态行的展开（只读详情）
  const [expandedStatic, setExpandedStatic] = useState<string | null>(null)
  // clsFilter 首载由 load 覆盖，仅后续变化时重新拉取
  const clsLoadedOnce = useRef(false)

  const loadChanges = (classification: ChangeCls) => {
    // 分类筛选走服务端过滤：本地只存最近 20 条，前端过滤会漏更早的 BREAKING
    getToolContractChanges({ classification: classification || undefined })
      .then((ch) => setChanges(ch.changes))
      .catch(() => setChanges([]))
  }

  // clsFilter 的 ref 视图：load 保持稳定引用（仅挂载与探针后调用），
  // 变更历史分类切换由下方专用 effect 处理（与原实现一致，避免双拉取）
  const clsFilterRef = useRef(clsFilter)
  clsFilterRef.current = clsFilter

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    Promise.all([getToolInventory(), getToolStats()])
      .then(([inv, st]) => {
        setInventory(inv)
        setStats(st)
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
    loadChanges(clsFilterRef.current)
    getMcpTools()
      .then((tools) => setMcpTools(tools))
      .catch((e: Error) => setMcpError(e.message))
  }, [])

  useEffect(load, [load])

  // 分类切换重新拉取（首载已由 load 覆盖）
  useEffect(() => {
    if (!clsLoadedOnce.current) {
      clsLoadedOnce.current = true
      return
    }
    loadChanges(clsFilter)
  }, [clsFilter])

  // 浏览器后退/外链带参时回填筛选状态（同值 set 不触发渲染，无循环）
  useEffect(() => {
    const params = new URLSearchParams(searchParams.toString())
    const next = {
      q: params.get('q') ?? '',
      ds: params.get('ds') ?? 'all',
      failed: params.get('failed') === '1',
    }
    setQuery((c) => (c === next.q ? c : next.q))
    setDsFilter((c) => (c === next.ds ? c : next.ds))
    setFailedOnly((c) => (c === next.failed ? c : next.failed))
    setClsFilter((c) => {
      const parsed = parseCls(params.get('cls'))
      return c === parsed ? c : parsed
    })
  }, [searchParams])

  const syncFilterUrl = (patch: Record<string, string | null>) => {
    const params = new URLSearchParams(searchParams.toString())
    for (const [k, v] of Object.entries(patch)) {
      if (v) params.set(k, v)
      else params.delete(k)
    }
    router.replace(`?${params.toString()}`, { scroll: false })
  }

  // 契约清单 × 运行统计合并（runtime 已在后端合并，这里只排序展示）
  const tools = useMemo(() => inventory?.tools ?? [], [inventory])

  const filteredTools = useMemo(() => {
    const kw = query.trim().toLowerCase()
    return tools.filter((t) => {
      if (failedOnly && !((t.runtime?.failures ?? 0) > 0)) return false
      if (dsFilter !== 'all' && (t.data_source?.type ?? 'internal') !== dsFilter) return false
      if (!kw) return true
      return [t.name, t.display_name, t.module, ...t.capabilities]
        .join('\n')
        .toLowerCase()
        .includes(kw)
    })
  }, [tools, query, dsFilter, failedOnly])

  // registry 统一目录：静态契约行 + 动态工具行共用搜索词
  const filteredMcpTools = useMemo(() => {
    const kw = query.trim().toLowerCase()
    if (!kw) return mcpTools
    return mcpTools.filter((t) =>
      [t.capability, t.tool_name, t.connection_name ?? '', t.description]
        .join('\n')
        .toLowerCase()
        .includes(kw))
  }, [mcpTools, query])

  const pendingMcpCount = mcpTools.filter((t) => t.status === 'pending').length
  const driftMcpCount = mcpTools.filter((t) => t.schema_changed).length
  const approvedMcpCount = mcpTools.filter((t) => t.status === 'approved').length

  const runProbe = async () => {
    const tool = probeTool || tools[0]?.name || ''
    if (!tool) {
      setProbeError('当前没有可用于探测的 Tool 契约')
      return
    }
    setProbeLoading(true)
    setProbeError(null)
    setProbeResult(null)
    try {
      const result = await runToolFailureProbe(tool, probeClass)
      setProbeResult({ trace_id: result.trace_id, status: result.status })
      load()
    } catch (e) {
      setProbeError(e instanceof Error ? e.message : '失败探针执行失败')
    } finally {
      setProbeLoading(false)
    }
  }

  const headerActions = (
    <>
      <Link
        href="/skills"
        className="inline-flex min-h-9 items-center rounded-md border border-border-subtle px-3 text-[12px] text-text-secondary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        Skill 与 Capability
      </Link>
      <AssetActionButton icon={<RefreshCw size={13} className={loading ? 'animate-spin' : ''} />} onClick={load}>
        刷新
      </AssetActionButton>
    </>
  )

  return (
    <WorkbenchShell
      title="Tool 治理"
      description={`契约快照 tool_contracts.lock.json（${inventory?.lock_git_sha ?? '…'}）× 运行统计（进程内 Prometheus 直读）× 动态工具（MCP 发现 + HTTP API 登记）`}
      tabs={TOOL_PAGE_TABS}
      activeTab={tab}
      onTabChange={selectTab}
      actions={headerActions}
    >
      <div className="mx-auto w-full max-w-7xl space-y-4 px-4 py-5 sm:px-6">
        {tab === 'registry' && (
          <>
            <StatMetricBar items={[
              {
                label: '工具总数',
                value: tools.length + mcpTools.length,
                hint: `静态契约 ${tools.length} + 动态 ${mcpTools.length}`,
              },
              {
                label: '运行时可用',
                value: (stats?.totals.tools_seen ?? 0) + approvedMcpCount,
                hint: `静态观测 ${stats?.totals.tools_seen ?? '—'} + 动态已生效 ${approvedMcpCount}`,
              },
              {
                label: '待审核', value: pendingMcpCount,
                tone: pendingMcpCount > 0 ? 'warn' : 'default',
                hint: pendingMcpCount > 0 ? '动态工具等待审核' : undefined,
              },
              {
                label: '契约漂移', value: driftMcpCount,
                tone: driftMcpCount > 0 ? 'danger' : 'default',
                hint: driftMcpCount > 0 ? '上游 Schema 已变化，需重审' : undefined,
              },
            ]} />

            {error && <ErrorState message={error} onRetry={load} />}

            {inventory?.lock_error && (
              <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-[13px] text-amber-800">
                <AlertTriangle size={16} /> 契约 lock 读取失败：{inventory.lock_error}
              </div>
            )}

            {mcpError && (
              <div className="flex flex-wrap items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-[13px] text-amber-800">
                <AlertTriangle size={16} /> 动态工具目录加载失败：{mcpError}
                <AssetActionButton onClick={load}>重试</AssetActionButton>
              </div>
            )}

            <div className="flex flex-wrap items-center gap-2">
              <div className="relative">
                <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted/60" />
                <input
                  aria-label="搜索工具"
                  value={query}
                  onChange={(e) => {
                    setQuery(e.target.value)
                    syncFilterUrl({ q: e.target.value.trim() || null })
                  }}
                  placeholder="搜索名称 / Capability / 连接"
                  className="min-w-[220px] rounded-md border border-border-subtle bg-surface-base py-1.5 pl-8 pr-2.5 text-[12px] text-text-primary"
                />
              </div>
              <span className="text-[11px] text-text-muted">
                {filteredTools.length + filteredMcpTools.length} / {tools.length + mcpTools.length} 个
              </span>
            </div>

            <AssetSection
              title="工具目录（静态契约 + 动态工具）"
              meta={`静态 ${filteredTools.length} · 动态 ${filteredMcpTools.length}`}
              bodyClassName="p-0"
            >
              {loading && tools.length === 0 ? (
                <div className="p-4"><Skeleton rows={6} cols={5} /></div>
              ) : filteredTools.length + filteredMcpTools.length === 0 ? (
                <EmptyState
                  title={tools.length + mcpTools.length > 0 ? '匹配筛选的工具' : '工具'}
                  description="调整搜索词；动态工具需先在连接中心完成发现与登记"
                />
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-[12.5px]">
                    <thead>
                      <tr className="border-b border-border-subtle bg-bg-elevated/60 text-left text-[11px] text-text-muted">
                        <th className="px-4 py-2 font-normal">工具 / 用途</th>
                        <th className="px-2 py-2 font-normal">来源</th>
                        <th className="px-2 py-2 font-normal">业务域</th>
                        <th className="px-2 py-2 font-normal">风险</th>
                        <th className="px-2 py-2 font-normal">审核 / 状态</th>
                        <th className="px-4 py-2 text-right font-normal">操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredTools.map((t) => {
                        const expanded = expandedStatic === t.name
                        return (
                          <FragmentRow
                            key={t.name}
                            expanded={expanded}
                            onToggle={() => setExpandedStatic(expanded ? null : t.name)}
                            name={t.name}
                            displayName={t.display_name}
                            module={t.module}
                            source={
                              t.data_source ? <DataSourceBadge ds={t.data_source} /> : (
                                <span className="rounded px-1 py-0.5 text-[10px] font-medium text-text-muted" title="平台内部 Tool（契约 lock）">
                                  Python
                                </span>
                              )
                            }
                            domain="—"
                            risk="—"
                            status={<StatusChip tone="neutral">契约锁定</StatusChip>}
                            action={
                              <button type="button" className="inline-flex items-center text-[11px] text-text-muted hover:text-text-primary"
                                aria-expanded={expanded}>
                                {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                                详情
                              </button>
                            }
                          >
                            <StaticToolDetail tool={t} />
                          </FragmentRow>
                        )
                      })}
                      {filteredMcpTools.map((t) => {
                        const meta = MCP_STATUS_META[t.status] ?? { label: t.status, tone: 'neutral' as StatusTone }
                        return (
                          <tr key={`mcp-${t.id}`} className="border-b border-border-subtle hover:bg-slate-50/60">
                            <td className="px-4 py-2">
                              <div className="flex flex-wrap items-center gap-1.5">
                                <span className="text-[13px] font-medium text-text-primary">{t.capability}</span>
                                {t.schema_changed && (
                                  <span className="rounded bg-red-100 px-1 text-[10px] text-red-700" title="上游 Schema 已变化，需重新审核">漂移</span>
                                )}
                              </div>
                              <div className="font-mono text-[11px] text-text-muted">
                                {t.tool_name}
                                {t.description && <span className="ml-2 font-sans text-text-muted">{t.description.slice(0, 60)}{t.description.length > 60 ? '…' : ''}</span>}
                              </div>
                            </td>
                            <td className="px-2 py-2"><DynamicSourceBadge tool={t} /></td>
                            <td className="px-2 py-2 text-[12px]">{domainLabel(
                              typeof t.governance?.domain === 'string' ? t.governance.domain : '')}</td>
                            <td className="px-2 py-2 text-[12px]">{t.governance?.risk_level ?? '—'}</td>
                            <td className="px-2 py-2">
                              <div className="flex flex-wrap items-center gap-1.5">
                                <StatusChip tone={meta.tone}>{meta.label}</StatusChip>
                              </div>
                            </td>
                            <td className="px-4 py-2 text-right">
                              <AssetActionButton onClick={() => setDrawerToolId(t.id)}>详情</AssetActionButton>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </AssetSection>

            <p className="flex items-center gap-1.5 text-[11px] text-text-muted">
              <CheckCircle2 size={12} />
              静态行（Python）来自契约 lock，展开查看数据源/额度/契约参数；动态行（MCP / HTTP API）来自连接发现与登记，「详情」打开抽屉完成审核、执行策略、授权与测试调用；连接与凭据在「连接中心」管理
            </p>
          </>
        )}

        {tab === 'quality' && (
          <>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <AssetStatCard label="调用总数" value={fmtCount(stats?.totals.calls)} hint="进程内 Prometheus 累计" />
              <AssetStatCard
                label="成功率"
                value={stats?.totals.success_rate != null ? `${(stats.totals.success_rate * 100).toFixed(2)}%` : '—'}
                tone={stats?.totals.success_rate != null && stats.totals.success_rate < 0.99 ? 'warn' : 'default'}
                hint={stats?.totals.success_rate == null ? '暂无调用数据' : undefined}
              />
              <AssetStatCard
                label="失败次数"
                value={fmtCount(stats?.totals.failures)}
                tone={(stats?.totals.failures ?? 0) > 0 ? 'warn' : 'default'}
                hint={stats && stats.totals.failures > 0 ? '失败归属见下方「失败 Tool」区块' : '暂无失败'}
              />
              <AssetStatCard
                label="运行时观测到的 Tool"
                value={String(stats?.totals.tools_seen ?? '—')}
                hint="只计生产流量（探针/测试不计入）"
              />
            </div>

            <AssetSection title="强制失败测试" meta="只写模拟 Trace，不触碰真实上游" icon={<ShieldCheck size={15} />} bodyClassName="pt-3">
              <div className="space-y-2">
                <div className="text-[12px] leading-relaxed text-text-secondary">
                  按七分类口径生成一条模拟失败的 tool_call Trace（error_class 由探针落盘，status 随分类映射），
                  用于验证失败链路、告警与看板；不影响真实 Tool 与运行时快照。
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <select
                    aria-label="探测 Tool"
                    value={probeTool || tools[0]?.name || ''}
                    onChange={(e) => setProbeTool(e.target.value)}
                    className="min-w-[240px] rounded-md border border-border-subtle bg-surface-base px-2.5 py-1.5 text-[12px] text-text-primary"
                  >
                    {tools.length === 0 && <option value="">暂无 Tool 契约</option>}
                    {tools.map((tool) => <option key={tool.name} value={tool.name}>{tool.display_name || tool.name}</option>)}
                  </select>
                  <select
                    aria-label="探测错误分类"
                    value={probeClass}
                    onChange={(e) => setProbeClass(e.target.value as ToolFailureClass)}
                    className="rounded-md border border-border-subtle bg-surface-base px-2.5 py-1.5 text-[12px] text-text-primary"
                  >
                    {FAILURE_PROBE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                  </select>
                  <button
                    type="button"
                    onClick={runProbe}
                    disabled={probeLoading || tools.length === 0}
                    className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-[12px] font-medium text-white hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {probeLoading && <RefreshCw size={13} className="animate-spin" />}
                    {probeLoading ? '生成中…' : '生成失败 Trace'}
                  </button>
                </div>
                {probeError && <div className="text-[12px] text-red-700">{probeError}</div>}
                {probeResult && (
                  <div className="flex flex-wrap items-center gap-2 text-[12px] text-emerald-700">
                    <span>已生成模拟失败：{ERROR_CLASS_LABEL[probeClass]}（{probeResult.status}）</span>
                    <a href={`/observability/traces/${probeResult.trace_id}`} className="text-accent hover:underline">
                      查看 Trace：{probeResult.trace_id.slice(0, 12)} →
                    </a>
                  </div>
                )}
              </div>
            </AssetSection>

            <FailureAnalysisSection />

            {stats && stats.top_failed_tools.length > 0 && (
              <AssetSection title="失败 Tool" meta="含未登记契约名" icon={<AlertTriangle size={15} />} bodyClassName="pt-3">
                <ul className="space-y-1.5">
                  {stats.top_failed_tools.map((f) => {
                    const inLock = (inventory?.tools ?? []).some((t) => t.name === f.tool)
                    return (
                      <li key={f.tool} className="flex flex-wrap items-center gap-2 text-[12px]">
                        <span className="font-mono text-text-primary">{f.tool}</span>
                        {!inLock && (
                          <span
                            className="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] text-amber-700"
                            title="该名字不在契约 lock 清单中：多为 capability 名（历史调用点记账）或测试流量，不会出现在契约清单行"
                          >
                            未登记契约名
                          </span>
                        )}
                        <span className="font-mono text-red-600">失败 {fmtCount(f.failures)}</span>
                        <span className="text-text-muted">/ 调用 {fmtCount(f.calls)}</span>
                        {Object.keys(f.error_classes ?? {}).length > 0 && (
                          <span className="text-text-muted">
                            {Object.entries(f.error_classes)
                              .sort((a, b) => b[1] - a[1])
                              .map(([cls, n]) => `${ERROR_CLASS_LABEL[cls] ?? cls}:${fmtCount(n)}`)
                              .join(' · ')}
                          </span>
                        )}
                        <a
                          href={`/observability/monitoring?tab=traces&has_tool=${encodeURIComponent(f.tool)}`}
                          className="text-[11px] text-accent hover:underline"
                        >
                          Trace 下钻 →
                        </a>
                      </li>
                    )
                  })}
                </ul>
              </AssetSection>
            )}

            {stats && stats.top_error_classes.length > 0 && (
              <AssetSection title="失败原因分布" meta="统一七分类口径" icon={<ShieldCheck size={15} />} bodyClassName="pt-3">
                <div className="flex flex-wrap gap-2">
                  {stats.top_error_classes.map((c) => (
                    <span key={c.error_class} className="rounded-full bg-slate-100 px-2.5 py-1 text-[12px] text-text-secondary">
                      {ERROR_CLASS_LABEL[c.error_class] ?? c.error_class}
                      <span className="ml-1.5 font-mono font-semibold">{c.count}</span>
                    </span>
                  ))}
                </div>
              </AssetSection>
            )}
          </>
        )}

        {tab === 'contract' && (
          <>
            {inventory?.lock_error && (
              <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-[13px] text-amber-800">
                <AlertTriangle size={16} /> 契约 lock 读取失败：{inventory.lock_error}
              </div>
            )}

            {error && <ErrorState message={error} onRetry={load} />}

            <div className="flex flex-wrap items-center gap-2">
              <div className="relative">
                <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted/60" />
                <input
                  aria-label="搜索工具"
                  value={query}
                  onChange={(e) => {
                    setQuery(e.target.value)
                    syncFilterUrl({ q: e.target.value.trim() || null })
                  }}
                  placeholder="搜索名称 / 模块 / Capability"
                  className="min-w-[220px] rounded-md border border-border-subtle bg-surface-base py-1.5 pl-8 pr-2.5 text-[12px] text-text-primary"
                />
              </div>
              <select
                aria-label="数据源筛选"
                value={dsFilter}
                onChange={(e) => {
                  setDsFilter(e.target.value)
                  syncFilterUrl({ ds: e.target.value !== 'all' ? e.target.value : null })
                }}
                className="rounded-md border border-border-subtle bg-surface-base px-2.5 py-1.5 text-[12px] text-text-primary"
              >
                {DS_FILTER_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
              <label className="flex min-h-9 items-center gap-1.5 text-[12px] text-text-secondary">
                <input
                  type="checkbox"
                  checked={failedOnly}
                  onChange={(e) => {
                    setFailedOnly(e.target.checked)
                    syncFilterUrl({ failed: e.target.checked ? '1' : null })
                  }}
                />
                仅看失败
              </label>
              <span className="text-[11px] text-text-muted">
                {filteredTools.length} / {tools.length} 个
              </span>
            </div>

            <AssetSection title="Tool 契约清单" meta={`${filteredTools.length}/${tools.length} 个`} bodyClassName="p-0">
              {loading && tools.length === 0 ? (
                <div className="p-4"><Skeleton rows={6} cols={5} /></div>
              ) : (
                <>
                  <table className="w-full">
                    <thead>
                      <tr className="border-b border-border-subtle bg-bg-elevated/60 text-left text-[11px] uppercase tracking-wide text-text-muted">
                        <th className="px-3 py-2">工具</th>
                        <th className="px-3 py-2">模块</th>
                        <th className="px-3 py-2">Capability 归属</th>
                        <th className="px-3 py-2">契约指纹</th>
                        <th className="px-3 py-2 text-right">调用</th>
                        <th className="px-3 py-2 text-right">成功率</th>
                        <th className="px-3 py-2 text-right">失败</th>
                        <th className="px-3 py-2">错误类</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-black/[0.04]">
                      {filteredTools.map((t) => (
                        <ToolRow key={t.name} tool={t} />
                      ))}
                    </tbody>
                  </table>
                  {filteredTools.length === 0 && !loading && (
                    <EmptyState
                      title={tools.length > 0 ? '没有匹配筛选的 Tool' : '暂无 Tool 契约数据'}
                      description={tools.length > 0 ? '调整搜索词 / 数据源 / 失败筛选' : '请先运行 gen_tool_contract_lock 生成 lock'}
                    />
                  )}
                </>
              )}
            </AssetSection>

            <p className="flex items-center gap-1.5 text-[11px] text-text-muted">
              <CheckCircle2 size={12} />
              中文名在 backend/tools/labels.py 登记、随契约 lock 派生（G2 禁手抄）；红色失败行点击可展开错误来源明细；「不可静态派生」= 该 Skill 的 Tool 分发逻辑无法从 _tool_fn 主链求值（如 SQL/Competitor）；数据源徽章：MCP=外部 MCP server 数据源（tooltip 含上游工具与开关）、外部=外部网络 API，无徽章 = 平台内部数据源
            </p>

            <AssetSection title="契约变更历史" meta="生成器检测到变更时自动落库，最新在前" bodyClassName="p-0">
              <div className="border-b border-border-subtle px-4 py-2.5">
                <select
                  aria-label="变更分类筛选"
                  value={clsFilter}
                  onChange={(e) => {
                    // select 选项本身来自白名单，as 收窄安全
                    setClsFilter(e.target.value as ChangeCls)
                    syncFilterUrl({ cls: e.target.value || null })
                  }}
                  className="rounded-md border border-border-subtle bg-surface-base px-2.5 py-1.5 text-[12px] text-text-primary"
                >
                  {CLS_FILTER_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </div>
              {changes.length > 0 ? (
                <ul className="divide-y divide-black/[0.04]">
                  {changes.map((ch) => {
                    const meta = CLASS_META[ch.classification] ?? CLASS_META.INIT
                    return (
                      <li key={ch.id} className="px-4 py-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className={`rounded px-1.5 py-0.5 font-mono text-[11px] font-semibold ${meta.cls}`}>
                            {meta.label}
                          </span>
                          <span className="font-mono text-[11px] text-text-muted">
                            {ch.created_at.replace('T', ' ').slice(0, 19)}
                          </span>
                          <span className="font-mono text-[11px] text-text-secondary">git:{ch.git_sha}</span>
                          <span className="text-[11px] text-text-muted">{ch.tool_count} Tool</span>
                          <span className="text-[11px] text-text-muted/70">via {ch.detected_by}</span>
                        </div>
                        {ch.changed_tools.length > 0 && (
                          <ul className="mt-1.5 space-y-0.5">
                            {ch.changed_tools.map((t) => (
                              <li key={t.tool} className="text-[12px] text-text-secondary">
                                <span className="font-mono text-text-primary">{t.tool}</span>
                                {t.changes && t.changes.length > 0 && (
                                  <span className="ml-2 text-[11px] text-text-muted">
                                    {t.changes
                                      .map((c) => CHANGE_KIND_LABEL[c.kind] ?? c.kind + (c.param ? `:${c.param}` : ''))
                                      .join(' · ')}
                                  </span>
                                )}
                              </li>
                            ))}
                          </ul>
                        )}
                      </li>
                    )
                  })}
                </ul>
              ) : (
                <div className="px-4 py-6 text-center text-[12px] text-text-muted">
                  该分类下暂无变更记录（服务端按分类过滤，最近 20 条范围内无匹配）
                </div>
              )}
            </AssetSection>
          </>
        )}
      </div>

      {drawerToolId != null && (
        <ToolDrawer
          toolId={drawerToolId}
          onClose={() => setDrawerToolId(null)}
          onMutated={async () => {
            load()
          }}
        />
      )}
    </WorkbenchShell>
  )
}

/** registry 静态行：行 + 可展开只读详情（数据源/契约参数/错误明细）。 */
function FragmentRow({ expanded, onToggle, name, displayName, module, source, domain, risk, status, action, children }: {
  expanded: boolean
  onToggle: () => void
  name: string
  displayName: string
  module: string
  source: ReactNode
  domain: string
  risk: string
  status: ReactNode
  action: ReactNode
  children: ReactNode
}) {
  return (
    <>
      <tr className="cursor-pointer border-b border-border-subtle hover:bg-slate-50/60" onClick={onToggle}>
        <td className="px-4 py-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[13px] font-medium text-text-primary">
              {displayName || <span className="text-text-muted/60">（未登记中文名）</span>}
            </span>
          </div>
          <div className="font-mono text-[11px] text-text-muted" title={module}>
            {name}
            <span className="ml-2 font-sans text-text-muted/70">{module.replace(/^backend\//, '')}</span>
          </div>
        </td>
        <td className="px-2 py-2">{source}</td>
        <td className="px-2 py-2 text-[12px] text-text-muted">{domain}</td>
        <td className="px-2 py-2 text-[12px] text-text-muted">{risk}</td>
        <td className="px-2 py-2">{status}</td>
        <td className="px-4 py-2 text-right" onClick={(e) => e.stopPropagation()}>
          <span onClick={onToggle}>{action}</span>
        </td>
      </tr>
      {expanded && (
        <tr className="bg-slate-50/60">
          <td colSpan={6} className="px-10 py-3">{children}</td>
        </tr>
      )}
    </>
  )
}
