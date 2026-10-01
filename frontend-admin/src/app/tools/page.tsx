'use client'

/**
 * /tools — Tool 治理中心（治理 M1/M2，2026-09-30）
 *
 * 数据源：GET /api/admin/tools（契约 lock 合并视图）+ /api/admin/tools/stats
 * （进程内 Prometheus 直读聚合）。事实源在代码 + tool_contracts.lock.json，
 * 本页只读不写。失败 Tool 行点击跳转 /observability/traces 下钻（预置过滤）。
 */
import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, RefreshCw, ShieldCheck, Wrench } from 'lucide-react'
import PageHeader from '@/components/layout/PageHeader'
import ErrorState from '@/components/shared/ErrorState'
import EmptyState from '@/components/shared/EmptyState'
import {
  getToolContractChanges,
  getToolErrors,
  getToolInventory,
  getToolStats,
  type ToolContractChange,
  type ToolContractEntry,
  type ToolErrorRecord,
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

function StatCard({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: 'ok' | 'warn' }) {
  return (
    <div className="rounded-lg border border-black/5 bg-white p-4">
      <div className="text-[11px] text-text-muted">{label}</div>
      <div className={`mt-1 text-xl font-semibold ${tone === 'warn' ? 'text-amber-600' : 'text-text-primary'}`}>
        {value}
      </div>
      {hint && <div className="mt-0.5 text-[11px] text-text-muted/70">{hint}</div>}
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

  // 失败行展开错误来源：懒加载（首次展开才请求），端点失败降级为空列表不阻塞
  const toggle = () => {
    if (!failed) return
    const next = !expanded
    setExpanded(next)
    if (next && errors === null) {
      setLoadingErr(true)
      getToolErrors(tool.name, 10)
        .then((r) => setErrors(r.errors))
        .catch(() => setErrors([]))
        .finally(() => setLoadingErr(false))
    }
  }

  return (
    <>
      <tr className={`${failed ? 'bg-red-50/50 cursor-pointer' : ''} hover:bg-slate-50/60`} onClick={failed ? toggle : undefined}>
        <td className="px-3 py-2">
          <div className="flex items-center gap-1">
            {failed && (expanded ? <ChevronDown size={13} className="text-red-500" /> : <ChevronRight size={13} className="text-red-500" />)}
            <span className="text-[13px] font-medium text-text-primary">
              {tool.display_name || <span className="text-text-muted/60">（未登记中文名）</span>}
            </span>
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
              href={`/observability/traces?has_tool=${encodeURIComponent(tool.name)}`}
              className="text-[11px] text-blue-600 hover:underline"
            >
              Trace 下钻 →
            </a>
          </td>
        )}
      </tr>
      {expanded && failed && (
        <tr className="bg-red-50/30">
          <td colSpan={9} className="px-10 py-3">
            <div className="text-[12px] font-medium text-text-secondary mb-2">
              错误来源明细（最近 10 条，来自执行 Trace）
            </div>
            {loadingErr && <div className="text-[12px] text-text-muted">加载中…</div>}
            {!loadingErr && errors && errors.length === 0 && (
              <div className="text-[12px] text-text-muted">
                最近 Trace 中没有该 Tool 的失败记录（可能发生在更早时间窗，或由探针/测试流量产生）
              </div>
            )}
            {!loadingErr && errors && errors.length > 0 && (
              <ul className="space-y-1.5">
                {errors.map((e, i) => (
                  <li key={`${e.trace_id}-${i}`} className="text-[12px] leading-relaxed">
                    <span className="font-mono text-[11px] text-text-muted">{e.ts.replace('T', ' ').slice(0, 19)}</span>
                    {'  '}
                    {e.skill && <span className="text-text-secondary">经 {e.skill}</span>}
                    {e.capability && <span className="font-mono text-[11px] text-text-muted">（{e.capability}）</span>}
                    {'  '}
                    <span className="text-red-700">{e.error_code || '失败'}</span>
                    {e.error && <span className="text-text-secondary">：{e.error}</span>}
                    {'  '}
                    <a href={`/observability/traces/${e.trace_id}`} className="text-blue-600 hover:underline">
                      查看完整 Trace
                    </a>
                  </li>
                ))}
              </ul>
            )}
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

  return (
    <div className="mx-auto max-w-7xl px-6 py-8">
      <div className="mb-6 flex items-start justify-between">
        <PageHeader
          title="Tool 治理中心"
          desc={`契约快照 tool_contracts.lock.json（${inventory?.lock_git_sha ?? '…'}）× 运行统计（进程内 Prometheus 直读）`}
        />
        <button
          onClick={load}
          className="mt-1 inline-flex items-center gap-1.5 rounded-md border border-black/10 px-3 py-1.5 text-[13px] text-text-secondary hover:bg-black/[0.03]"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> 刷新
        </button>
      </div>

      {inventory?.lock_error && (
        <div className="mb-4 flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-[13px] text-amber-800">
          <AlertTriangle size={16} /> 契约 lock 读取失败：{inventory.lock_error}
        </div>
      )}

      {error && <ErrorState message={error} onRetry={load} />}

      {!error && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <StatCard label="Tool 总数（契约 lock）" value={String(inventory?.count ?? '—')} hint="与代码同步生成的契约快照" />
            <StatCard
              label="运行时观测到的 Tool"
              value={String(stats?.totals.tools_seen ?? '—')}
              hint="只计生产流量（探针/测试不计入）"
            />
            <StatCard label="调用总数" value={fmtCount(stats?.totals.calls)} hint="进程内 Prometheus 累计" />
            <StatCard
              label="成功率"
              value={stats?.totals.success_rate != null ? `${(stats.totals.success_rate * 100).toFixed(2)}%` : '—'}
              tone={stats?.totals.success_rate != null && stats.totals.success_rate < 0.99 ? 'warn' : 'ok'}
              hint={stats?.totals.success_rate == null ? '暂无调用数据' : undefined}
            />
            <StatCard
              label="失败次数"
              value={fmtCount(stats?.totals.failures)}
              tone={(stats?.totals.failures ?? 0) > 0 ? 'warn' : 'ok'}
              hint="点击下方失败行可看错误来源"
            />
          </div>

          {stats && stats.top_error_classes.length > 0 && (
            <div className="mt-4 rounded-lg border border-black/5 bg-white p-4">
              <div className="mb-2 flex items-center gap-1.5 text-[13px] font-medium text-text-primary">
                <ShieldCheck size={15} /> 失败原因分布（统一七分类口径）
              </div>
              <div className="flex flex-wrap gap-2">
                {stats.top_error_classes.map((c) => (
                  <span key={c.error_class} className="rounded-full bg-slate-100 px-2.5 py-1 text-[12px] text-text-secondary">
                    {ERROR_CLASS_LABEL[c.error_class] ?? c.error_class}
                    <span className="ml-1.5 font-mono font-semibold">{c.count}</span>
                  </span>
                ))}
              </div>
            </div>
          )}

          <div className="mt-6 overflow-hidden rounded-lg border border-black/5 bg-white">
            <table className="w-full">
              <thead>
                <tr className="border-b border-black/5 bg-slate-50/60 text-left text-[11px] uppercase tracking-wide text-text-muted">
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
          </div>

          <p className="mt-3 flex items-center gap-1.5 text-[11px] text-text-muted">
            <CheckCircle2 size={12} />
            中文名在 backend/tools/labels.py 登记、随契约 lock 派生（G2 禁手抄）；红色失败行点击可展开错误来源明细；「不可静态派生」= 该 Skill 的 Tool 分发逻辑无法从 _tool_fn 主链求值（如 SQL/Competitor）
          </p>

          {changes.length > 0 && (
            <div className="mt-6 overflow-hidden rounded-lg border border-black/5 bg-white">
              <div className="border-b border-black/5 bg-slate-50/60 px-4 py-2.5 text-[13px] font-medium text-text-primary">
                契约变更历史（生成器检测到变更时自动落库，最新在前）
              </div>
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
            </div>
          )}
        </>
      )}
    </div>
  )
}
