'use client'

/**
 * /tools — Tool 治理中心（治理 M1/M2，2026-09-30）
 *
 * 数据源：GET /api/admin/tools（契约 lock 合并视图）+ /api/admin/tools/stats
 * （进程内 Prometheus 直读聚合）。事实源在代码 + tool_contracts.lock.json，
 * 清单/统计仍是只读；强制失败测试只写入明确标记的模拟 Trace，不触达真实 Tool。
 * 失败 Tool 行点击跳转运行监控工作台的 Trace Tab（预置过滤）。
 */
import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, RefreshCw, ShieldCheck } from 'lucide-react'
import { AssetActionButton, AssetPageShell, AssetSection, AssetStatCard } from '@/components/layout/AssetPageShell'
import ErrorState from '@/components/shared/ErrorState'
import EmptyState from '@/components/shared/EmptyState'
import {
  getToolContractChanges,
  getToolErrors,
  getToolInventory,
  getToolStats,
  runToolFailureProbe,
  type ToolContractChange,
  type ToolContractEntry,
  type ToolErrorRecord,
  type ToolFailureClass,
  type ToolInventory,
  type ToolStats,
} from '@/api/governance'

const ERROR_CLASS_LABEL: Record<string, string> = {
  timeout: '超时',
  network_error: '网络',
  permission_denied: '权限',
  validation_error: '校验',
  business_error: '业务',
  contract_error: '契约',
  provider_error: '供应商',
  unknown: '未知',
}

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

const DATA_SOURCE_META: Record<string, { label: string; cls: string }> = {
  mcp: { label: 'MCP', cls: 'bg-violet-100 text-violet-700' },
  rest: { label: '外部', cls: 'bg-sky-100 text-sky-700' },
}

/** 数据源徽章：MCP 带「上游·开关」tooltip；rest 带 provider；internal 不显示（避免全表噪音）。 */
function DataSourceBadge({ ds }: { ds: NonNullable<ToolContractEntry['data_source']> }) {
  if (ds.type === 'internal') return null
  const meta = DATA_SOURCE_META[ds.type]
  if (!meta) return null
  const tip =
    ds.type === 'mcp'
      ? `外部 MCP server 数据源：${ds.provider ?? ''} · 上游工具 ${ds.upstream_tool ?? '—'}（开关 ${ds.switch_env ?? '—'}，关闭时该 Tool 明确报未配置）`
      : `外部网络 API：${ds.provider ?? ''}`
  return (
    <span className={`rounded px-1 py-0.5 text-[10px] font-medium whitespace-nowrap ${meta.cls}`} title={tip}>
      {meta.label}·{ds.provider}
    </span>
  )
}

const QUOTA_PERIOD_LABEL: Record<string, string> = {
  day: '按日',
  period: '按期',
  qps: '速率',
}

// 额度运行时三态+计数态的诚实口径：unlimited ≠ 已用 0（未配预算时不计数）
const QUOTA_STATUS_META: Record<string, { label: string; cls: string }> = {
  unlimited: { label: '未设预算（不限）', cls: 'bg-slate-100 text-slate-600' },
  ok: { label: '正常', cls: 'bg-emerald-100 text-emerald-700' },
  exhausted: { label: '已达预算（软停）', cls: 'bg-red-100 text-red-700' },
  tracked: { label: '计数中', cls: 'bg-sky-100 text-sky-700' },
  untracked: { label: '读数不可用', cls: 'bg-amber-100 text-amber-700' },
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

/** 契约参数表（lock args_schema）：参数 / 类型 / 必填 / 默认值。 */
function ToolArgsTable({ args }: { args: NonNullable<ToolContractEntry['args_schema']> }) {
  const params = Object.entries(args)
  if (params.length === 0) return <div className="text-[12px] text-text-muted">无参数</div>
  return (
    <table className="w-full text-[12px]">
      <thead>
        <tr className="text-left text-[11px] text-text-muted">
          <th className="py-1 pr-3 font-normal">参数</th>
          <th className="py-1 pr-3 font-normal">类型</th>
          <th className="py-1 pr-3 font-normal">必填</th>
          <th className="py-1 font-normal">默认值</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-black/[0.04]">
        {params.map(([name, spec]) => {
          const s = spec.schema ?? {}
          const type = typeof s.type === 'string'
            ? s.type
            : Array.isArray(s.anyOf)
              ? (s.anyOf as { type?: string }[]).map((v) => v.type ?? '?').join(' | ')
              : '—'
          const enumVals = Array.isArray(s.enum) ? (s.enum as unknown[]).map(String).join(' | ') : ''
          return (
            <tr key={name}>
              <td className="py-1 pr-3 font-mono text-[11px] text-text-primary">{name}</td>
              <td className="py-1 pr-3 text-text-secondary">
                {type}
                {enumVals && <span className="ml-1 text-text-muted">（{enumVals}）</span>}
              </td>
              <td className="py-1 pr-3">
                {spec.required ? <span className="text-red-600">必填</span> : <span className="text-text-muted">可选</span>}
              </td>
              <td className="py-1 font-mono text-[11px] text-text-muted">
                {spec.default !== '__unset__' ? JSON.stringify(spec.default) : '—'}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

/** 数据源与额度卡：声明（labels.py 经 lock 派生）+ 运行时读数（quota_runtime）。 */
function DataSourceQuotaSection({ tool }: { tool: ToolContractEntry }) {
  const ds = tool.data_source
  const q = ds?.quota
  const rt = tool.quota_runtime
  if (!ds) {
    return <div className="text-[12px] text-text-muted">数据源未登记（labels.py TOOL_DATA_SOURCES）</div>
  }
  const period = q?.period ? (QUOTA_PERIOD_LABEL[q.period] ?? q.period) : null
  return (
    <div className="space-y-1.5 text-[12px] leading-relaxed">
      <div className="flex flex-wrap items-center gap-2">
        {ds.type === 'internal' ? (
          <span className="text-text-secondary">平台内部数据源（无外部额度）</span>
        ) : (
          <>
            <span className="text-text-secondary">数据源：{ds.provider}</span>
            {ds.upstream_tool && <span className="font-mono text-[11px] text-text-muted">上游工具 {ds.upstream_tool}</span>}
            {ds.switch_env && <span className="font-mono text-[11px] text-text-muted">开关 {ds.switch_env}</span>}
          </>
        )}
      </div>
      {q && (
        <div className="flex flex-wrap items-center gap-2">
          {period && <span className="text-text-secondary">额度周期：{period}</span>}
          {q.limit != null && <span className="text-text-secondary">声明额度 {fmtCount(q.limit)} 次</span>}
          {q.limit_env && <span className="font-mono text-[11px] text-text-muted">预算 env {q.limit_env}</span>}
          {/* 纯被动声明（无计数器，如高德）不显示读数状态——note 已承载口径 */}
          {rt?.status && (q.usage_provider || q.usage_counter) && (
            <span className={`rounded px-1.5 py-0.5 text-[11px] ${QUOTA_STATUS_META[rt.status]?.cls ?? 'bg-slate-100 text-slate-600'}`}>
              {QUOTA_STATUS_META[rt.status]?.label ?? rt.status}
            </span>
          )}
          {/* 已设预算才显示用量：unlimited 态 current_usage 不计数，显示 0 会误导 */}
          {rt && (rt.status === 'ok' || rt.status === 'exhausted') && rt.usage != null && rt.budget != null && (
            <span className="font-mono text-text-primary">今日 {fmtCount(rt.usage)} / {fmtCount(rt.budget)}</span>
          )}
          {rt && rt.status === 'tracked' && rt.usage != null && (
            <span className="font-mono text-text-primary">本期已用 {fmtCount(rt.usage)} 次</span>
          )}
        </div>
      )}
      {q?.note && <div className="text-[11px] text-text-muted">{q.note}</div>}
      {rt?.status === 'untracked' && (q?.usage_provider || q?.usage_counter) && (
        <div className="text-[11px] text-amber-700">用量读数暂不可用（计数源不可达或未启用；额度声明仍有效）</div>
      )}
    </div>
  )
}

const fmtCount = (n?: number) => (n ?? 0).toLocaleString('zh-CN')

function ToolRow({ tool }: { tool: ToolContractEntry }) {
  const rt = tool.runtime
  const failed = (rt?.failures ?? 0) > 0
  const [expanded, setExpanded] = useState(false)
  const [errors, setErrors] = useState<ToolErrorRecord[] | null>(null)
  const [loadingErr, setLoadingErr] = useState(false)

  // 行点击展开详情（数据源与额度 + 契约参数）；失败行额外懒加载错误来源
  const toggle = () => {
    const next = !expanded
    setExpanded(next)
    if (next && failed && errors === null) {
      setLoadingErr(true)
      getToolErrors(tool.name, 10)
        .then((r) => setErrors(r.errors))
        .catch(() => setErrors([]))
        .finally(() => setLoadingErr(false))
    }
  }

  const chevronCls = failed ? 'text-red-500' : 'text-text-muted/50'

  return (
    <>
      <tr className={`${failed ? 'bg-red-50/50' : ''} cursor-pointer hover:bg-slate-50/60`} onClick={toggle}>
        <td className="px-3 py-2">
          <div className="flex items-center gap-1">
            {expanded ? <ChevronDown size={13} className={chevronCls} /> : <ChevronRight size={13} className={chevronCls} />}
            <span className="text-[13px] font-medium text-text-primary">
              {tool.display_name || <span className="text-text-muted/60">（未登记中文名）</span>}
            </span>
            {tool.data_source && <DataSourceBadge ds={tool.data_source} />}
          </div>
          <div className="font-mono text-[11px] text-text-muted">{tool.name}</div>
        </td>
        <td className="px-3 py-2 text-[12px] text-text-muted">{tool.module.replace(/^backend\//, '')}</td>
        <td className="px-3 py-2 text-[12px]">
          {tool.capabilities.length > 0 ? (
            <span className="font-mono text-[11px] text-text-secondary">
              {tool.capabilities.join(', ')}
            </span>
          ) : (
            <span className="text-[11px] text-text-muted/60">—（不可静态派生）</span>
          )}
        </td>
        <td className="px-3 py-2 text-[12px]">
          <span className="font-mono text-[11px] text-text-secondary" title={tool.content_hash}>
            {tool.content_hash.slice(0, 8)}
          </span>
        </td>
        <td className="px-3 py-2 text-right font-mono text-[12px]">{fmtCount(rt?.calls)}</td>
        <td className="px-3 py-2 text-right font-mono text-[12px]">
          {rt?.success_rate != null ? `${(rt.success_rate * 100).toFixed(1)}%` : '—'}
        </td>
        <td className="px-3 py-2 text-right font-mono text-[12px]">
          {failed ? (
            <span className="text-red-600">{fmtCount(rt?.failures)}</span>
          ) : (
            <span className="text-emerald-600">0</span>
          )}
        </td>
        <td className="px-3 py-2 text-[11px] text-text-muted">
          {rt?.error_classes && Object.keys(rt.error_classes).length > 0
            ? Object.entries(rt.error_classes)
                .sort((a, b) => b[1] - a[1])
                .map(([cls, n]) => `${ERROR_CLASS_LABEL[cls] ?? cls}:${fmtCount(n)}`)
                .join(' · ')
            : '—'}
        </td>
        {failed && (
          <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
            <a
              href={`/observability/monitoring?tab=traces&has_tool=${encodeURIComponent(tool.name)}`}
              className="text-[11px] text-blue-600 hover:underline"
            >
              Trace 下钻 →
            </a>
          </td>
        )}
      </tr>
      {expanded && (
        <tr className={failed ? 'bg-red-50/30' : 'bg-slate-50/60'}>
          <td colSpan={failed ? 9 : 8} className="px-10 py-3">
            <div className="space-y-4">
              <section>
                <div className="mb-1.5 text-[12px] font-medium text-text-secondary">数据源与额度</div>
                <DataSourceQuotaSection tool={tool} />
              </section>
              <section>
                <div className="mb-1.5 text-[12px] font-medium text-text-secondary">
                  契约参数（tool_contracts.lock.json）
                </div>
                {tool.args_schema ? (
                  <ToolArgsTable args={tool.args_schema} />
                ) : (
                  <div className="text-[12px] text-text-muted">—</div>
                )}
              </section>
              {failed && (
                <section>
                  <div className="mb-1.5 text-[12px] font-medium text-text-secondary">
                    错误来源明细（最近 10 条，Skill → Capability → Tool，来自执行 Trace）
                  </div>
                  {loadingErr && <div className="text-[12px] text-text-muted">加载中…</div>}
                  {!loadingErr && errors && errors.length === 0 && (
                    <div className="text-[12px] text-text-muted">
                      最近 Trace 中没有该 Tool 的失败记录（可能发生在更早时间窗，或由探针/测试流量产生）
                    </div>
                  )}
                  {!loadingErr && errors && errors.length > 0 && (
                    <ul className="space-y-1.5">
                      {errors.map((e, i) => {
                        const errorKey = e.error_class || e.error_code
                        const errorLabel = errorKey
                          ? (ERROR_CLASS_LABEL[errorKey] ?? errorKey)
                          : '失败'
                        return (
                          <li key={`${e.trace_id}-${i}`} className="rounded-md border border-red-100 bg-surface-base px-3 py-2 text-[12px] leading-relaxed">
                            <div className="flex flex-wrap items-center gap-1.5">
                              <span className="font-mono text-[11px] text-text-muted">{e.ts.replace('T', ' ').slice(0, 19)}</span>
                              <span className="text-text-muted">·</span>
                              <span className="font-medium text-text-secondary">来源链路</span>
                              <span className="text-text-muted">Skill</span>
                              <code className="text-[11px] text-text-primary">{e.skill || '未知'}</code>
                              <span className="text-text-muted">→ Capability</span>
                              <code className="text-[11px] text-text-primary">{e.capability || '未知'}</code>
                              <span className="text-text-muted">→ Tool</span>
                              <code className="text-[11px] text-text-primary">{e.tool || tool.name}</code>
                            </div>
                            <div className="mt-1 text-text-secondary">
                              <span className="font-medium">用户问题：</span>{e.question || '未记录'}
                            </div>
                            <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px]">
                              <span className="font-medium text-red-700">错误分类：{errorLabel}</span>
                              {e.source_error_code && <code className="text-text-muted">({e.source_error_code})</code>}
                              {e.error && <span className="text-text-secondary">{e.error}</span>}
                              <span className="text-text-muted">耗时 {e.latency_ms} ms</span>
                              <a href={`/observability/traces/${e.trace_id}`} className="text-blue-600 hover:underline">
                                查看完整 Trace
                              </a>
                            </div>
                          </li>
                        )
                      })}
                    </ul>
                  )}
                </section>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

export default function ToolsPage() {
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

  const load = () => {
    setLoading(true)
    setError(null)
    Promise.all([getToolInventory(), getToolStats(), getToolContractChanges()])
      .then(([inv, st, ch]) => {
        setInventory(inv)
        setStats(st)
        setChanges(ch.changes)
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  // 契约清单 × 运行统计合并（runtime 已在后端合并，这里只排序展示）
  const tools = useMemo(() => inventory?.tools ?? [], [inventory])

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
      await load()
    } catch (e) {
      setProbeError(e instanceof Error ? e.message : '失败探针执行失败')
    } finally {
      setProbeLoading(false)
    }
  }

  return (
    <AssetPageShell
          title="Tool 治理中心"
          desc={`契约快照 tool_contracts.lock.json（${inventory?.lock_git_sha ?? '…'}）× 运行统计（进程内 Prometheus 直读）`}
          actions={
            <AssetActionButton icon={<RefreshCw size={13} className={loading ? 'animate-spin' : ''} />} onClick={load}>
              刷新
            </AssetActionButton>
          }
    >

      {inventory?.lock_error && (
        <div className="mb-4 flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-[13px] text-amber-800">
          <AlertTriangle size={16} /> 契约 lock 读取失败：{inventory.lock_error}
        </div>
      )}

      {error && <ErrorState message={error} onRetry={load} className="rounded-xl border border-border-subtle bg-surface-base shadow-card" />}

      {!error && (
        <>
          <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <AssetStatCard label="Tool 总数（契约 lock）" value={String(inventory?.count ?? '—')} hint="与代码同步生成的契约快照" />
            <AssetStatCard
              label="运行时观测到的 Tool"
              value={String(stats?.totals.tools_seen ?? '—')}
              hint="只计生产流量（探针/测试不计入）"
            />
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
          </div>

          <AssetSection title="强制失败测试" meta="仅开发/测试环境" icon={<AlertTriangle size={15} />} bodyClassName="pt-3">
            <div className="space-y-2.5">
              <div className="text-[12px] leading-relaxed text-text-secondary">
                只生成模拟 ToolResult、指标和 Trace，不调用真实 Tool 或外部服务；用于稳定验收不同错误分类的展示与下钻。
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
                  <a href={`/observability/traces/${probeResult.trace_id}`} className="text-blue-600 hover:underline">
                    查看 Trace：{probeResult.trace_id.slice(0, 12)} →
                  </a>
                </div>
              )}
            </div>
          </AssetSection>

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
                          title="该名字不在契约 lock 清单中：多为 capability 名（历史调用点记账）或测试流量，不会出现在下方清单行"
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
                        className="text-[11px] text-blue-600 hover:underline"
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

          <AssetSection title="Tool 契约清单" meta={`${tools.length} 个`} bodyClassName="p-0">
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
                {tools.map((t) => (
                  <ToolRow key={t.name} tool={t} />
                ))}
              </tbody>
            </table>
            {tools.length === 0 && !loading && (
              <EmptyState title="暂无 Tool 契约数据" description="请先运行 gen_tool_contract_lock 生成 lock" />
            )}
          </AssetSection>

          <p className="mt-3 flex items-center gap-1.5 text-[11px] text-text-muted">
            <CheckCircle2 size={12} />
            中文名在 backend/tools/labels.py 登记、随契约 lock 派生（G2 禁手抄）；红色失败行点击可展开错误来源明细；「不可静态派生」= 该 Skill 的 Tool 分发逻辑无法从 _tool_fn 主链求值（如 SQL/Competitor）；数据源徽章：MCP=外部 MCP server 数据源（tooltip 含上游工具与开关）、外部=外部网络 API，无徽章 = 平台内部数据源
          </p>

          {changes.length > 0 && (
            <AssetSection title="契约变更历史" meta="生成器检测到变更时自动落库，最新在前" bodyClassName="p-0">
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
            </AssetSection>
          )}
          </div>
        </>
      )}
    </AssetPageShell>
  )
}
