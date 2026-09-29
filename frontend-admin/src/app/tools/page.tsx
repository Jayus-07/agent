'use client'

/**
 * /tools — Tool 治理中心（治理 M1/M2，2026-09-30）
 *
 * 数据源：GET /api/admin/tools（契约 lock 合并视图）+ /api/admin/tools/stats
 * （进程内 Prometheus 直读聚合）。事实源在代码 + tool_contracts.lock.json，
 * 本页只读不写。失败 Tool 行点击跳转 /observability/traces 下钻（预置过滤）。
 */
import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, RefreshCw, ShieldCheck, Wrench } from 'lucide-react'
import PageHeader from '@/components/layout/PageHeader'
import ErrorState from '@/components/shared/ErrorState'
import EmptyState from '@/components/shared/EmptyState'
import {
  getToolInventory,
  getToolStats,
  type ToolContractEntry,
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

function StatCard({ label, value, tone }: { label: string; value: string; tone?: 'ok' | 'warn' }) {
  return (
    <div className="rounded-lg border border-black/5 bg-white p-4">
      <div className="text-[11px] text-text-muted">{label}</div>
      <div className={`mt-1 text-xl font-semibold ${tone === 'warn' ? 'text-amber-600' : 'text-text-primary'}`}>
        {value}
      </div>
    </div>
  )
}

function ToolRow({ tool }: { tool: ToolContractEntry }) {
  const rt = tool.runtime
  const failed = (rt?.failures ?? 0) > 0
  return (
    <tr className={failed ? 'bg-red-50/50' : undefined}>
      <td className="px-3 py-2 font-mono text-[12px] text-text-primary">{tool.name}</td>
      <td className="px-3 py-2 text-[12px] text-text-muted">{tool.module}</td>
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
        <span className="font-mono text-[11px] text-text-secondary">{tool.content_hash}</span>
      </td>
      <td className="px-3 py-2 text-right font-mono text-[12px]">{rt?.calls ?? 0}</td>
      <td className="px-3 py-2 text-right font-mono text-[12px]">
        {rt?.success_rate != null ? `${(rt.success_rate * 100).toFixed(1)}%` : '—'}
      </td>
      <td className="px-3 py-2 text-right font-mono text-[12px]">
        {failed ? (
          <span className="text-red-600">{rt?.failures}</span>
        ) : (
          <span className="text-emerald-600">0</span>
        )}
      </td>
      <td className="px-3 py-2 text-[11px] text-text-muted">
        {rt?.error_classes && Object.keys(rt.error_classes).length > 0
          ? Object.entries(rt.error_classes)
              .sort((a, b) => b[1] - a[1])
              .map(([cls, n]) => `${ERROR_CLASS_LABEL[cls] ?? cls}:${n}`)
              .join(' · ')
          : '—'}
      </td>
      {failed && (
        <td className="px-3 py-2">
          <a
            href={`/observability/traces?has_tool=${encodeURIComponent(tool.name)}`}
            className="text-[11px] text-blue-600 hover:underline"
          >
            Trace 下钻 →
          </a>
        </td>
      )}
    </tr>
  )
}

export default function ToolsPage() {
  const [inventory, setInventory] = useState<ToolInventory | null>(null)
  const [stats, setStats] = useState<ToolStats | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const load = () => {
    setLoading(true)
    setError(null)
    Promise.all([getToolInventory(), getToolStats()])
      .then(([inv, st]) => {
        setInventory(inv)
        setStats(st)
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
            <StatCard label="Tool 总数（契约 lock）" value={String(inventory?.count ?? '—')} />
            <StatCard label="运行时观测到的 Tool" value={String(stats?.totals.tools_seen ?? '—')} />
            <StatCard label="调用总数" value={String(stats?.totals.calls ?? '—')} />
            <StatCard
              label="成功率"
              value={stats?.totals.success_rate != null ? `${(stats.totals.success_rate * 100).toFixed(2)}%` : '—'}
              tone={stats?.totals.success_rate != null && stats.totals.success_rate < 0.99 ? 'warn' : 'ok'}
            />
            <StatCard
              label="失败次数"
              value={String(stats?.totals.failures ?? '—')}
              tone={(stats?.totals.failures ?? 0) > 0 ? 'warn' : 'ok'}
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
                  <th className="px-3 py-2">Tool</th>
                  <th className="px-3 py-2">模块</th>
                  <th className="px-3 py-2">Capability 归属</th>
                  <th className="px-3 py-2">契约 Hash</th>
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
            契约与代码同源派生（G2 禁手抄）；「不可静态派生」= 该 Skill 的 Tool 分发逻辑无法从 _tool_fn 主链求值（如 SQL/Competitor）
          </p>
        </>
      )}
    </div>
  )
}
