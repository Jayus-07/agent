'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { RefreshCw } from 'lucide-react'
import { listTraces, getSystemHealth, type SystemHealthSnapshot } from '@/api/observability'
import { AssetActionButton, AssetPageShell, AssetSection, AssetState } from '@/components/layout/AssetPageShell'
import RoleGate from '@/components/auth/RoleGate'
import ErrorState from '@/components/shared/ErrorState'
import { buildRuntimeAlertSample, buildSystemHealthRows, getHealthSnapshotFreshness, type HealthProbeState } from '@/lib/observability/serviceHealth'
import { getRunStatusLabel, getRunStatusTone } from '@/lib/observability/runtimeStatus'
import type { TraceRecord } from '@/types/trace'

const HEALTH_LABEL: Record<HealthProbeState, string> = {
  available: '可用',
  unavailable: '不可用',
  degraded: '有异常',
  unknown: '未知',
}

const HEALTH_TONE: Record<HealthProbeState, string> = {
  available: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  unavailable: 'border-red-200 bg-red-50 text-red-800',
  degraded: 'border-amber-200 bg-amber-50 text-amber-800',
  unknown: 'border-slate-200 bg-slate-50 text-slate-600',
}

function formatDateTime(timestampMs: number): string {
  if (!Number.isFinite(timestampMs)) return '未采集'
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'medium', hour12: false }).format(timestampMs)
}

function SystemHealthContent() {
  const [health, setHealth] = useState<SystemHealthSnapshot | null>(null)
  const [traces, setTraces] = useState<TraceRecord[] | null>(null)
  const [healthError, setHealthError] = useState(false)
  const [traceError, setTraceError] = useState(false)
  const [initialLoading, setInitialLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [lastTraceFetchAt, setLastTraceFetchAt] = useState<number | null>(null)
  const requestSequence = useRef(0)

  const load = useCallback(async () => {
    const sequence = ++requestSequence.current
    setRefreshing(true)
    const [healthResult, traceResult] = await Promise.allSettled([getSystemHealth(), listTraces(200)])
    if (sequence !== requestSequence.current) return

    if (healthResult.status === 'fulfilled') {
      setHealth(healthResult.value)
      setHealthError(false)
    } else {
      setHealthError(true)
    }
    if (traceResult.status === 'fulfilled') {
      setTraces(traceResult.value)
      setTraceError(false)
      setLastTraceFetchAt(Date.now())
    } else {
      setTraceError(true)
    }
    setInitialLoading(false)
    setRefreshing(false)
  }, [])

  useEffect(() => {
    void load()
    return () => { requestSequence.current += 1 }
  }, [load])

  useEffect(() => {
    if (!autoRefresh) return
    const timer = window.setInterval(() => { void load() }, 60_000)
    return () => window.clearInterval(timer)
  }, [autoRefresh, load])

  const alerts = useMemo(() => buildRuntimeAlertSample(traces ?? []), [traces])
  const rows = useMemo(() => health ? buildSystemHealthRows(health) : [], [health])
  const freshness = health ? getHealthSnapshotFreshness(health) : 'unknown'
  const snapshotTime = health?.collected_at ? formatDateTime(health.collected_at * 1000) : '未返回采集时间'

  return (
    <AssetPageShell
      title="服务健康与告警"
      desc="健康状态来自受保护的服务快照；告警样本从当前账号获准读取的 Trace 记录生成。"
      actions={
        <>
          <label className="inline-flex min-h-9 items-center gap-2 px-2 text-[12px] text-text-secondary">
            <input type="checkbox" checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)} className="accent-accent" />
            每分钟刷新
          </label>
          <AssetActionButton disabled={refreshing} icon={<RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} />} onClick={() => void load()}>
            刷新
          </AssetActionButton>
          <Link href="/observability/monitoring?tab=traces" className="inline-flex min-h-9 items-center rounded-md border border-border-subtle px-3 text-[12px] text-text-secondary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
            执行记录
          </Link>
        </>
      }
    >
      <div className="space-y-4">
        <AssetSection title="服务健康快照" meta="来源：受保护的 system-health API" bodyClassName="p-0">
          {initialLoading && !health ? <AssetState>正在读取服务快照…</AssetState>
            : healthError && !health ? <ErrorState title="服务快照不可用" message="无法读取管理员健康快照。请检查权限或服务状态后重试。" onRetry={() => void load()} />
              : health ? <>
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle px-4 py-3 text-[11px] text-text-muted">
                  <span>采集时间：{snapshotTime}</span>
                  <span role="status" className={freshness === 'stale' ? 'font-medium text-amber-700' : freshness === 'fresh' ? 'text-emerald-700' : 'text-slate-500'}>
                    {freshness === 'fresh' ? '快照在 90 秒内' : freshness === 'stale' ? '快照已过期' : '采集时间未知'}
                  </span>
                </div>
                {healthError && <p role="alert" className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-[12px] text-amber-900">刷新失败，以下仍是上次成功读取的快照。</p>}
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[680px] text-left text-[12px]">
                    <thead><tr className="border-b border-border-subtle text-[11px] text-text-muted"><th className="px-4 py-2 font-medium">服务 / 观测项</th><th className="px-4 py-2 font-medium">状态</th><th className="px-4 py-2 font-medium">观测值</th></tr></thead>
                    <tbody>
                      {rows.map((row) => <tr key={row.key} className="border-b border-border-subtle last:border-0">
                        <th scope="row" className="px-4 py-3 font-medium text-text-primary">{row.label}</th>
                        <td className="px-4 py-3"><span className={`inline-flex rounded border px-2 py-0.5 text-[11px] ${HEALTH_TONE[row.state]}`}>{HEALTH_LABEL[row.state]}</span></td>
                        <td className="px-4 py-3 text-text-secondary">{row.value || '未采集'}</td>
                      </tr>)}
                    </tbody>
                  </table>
                </div>
                <p className="border-t border-border-subtle bg-bg-elevated/40 px-4 py-3 text-[11px] leading-relaxed text-text-muted">
                  此快照只覆盖 PostgreSQL、Redis、Celery Worker、任务统计和队列读数。APISIX、Prometheus/Grafana 与 LLM Provider 没有由该接口返回的可验证状态，因此不显示为正常。
                </p>
              </> : <AssetState>健康快照尚无数据。</AssetState>}
        </AssetSection>

        <AssetSection title="运行异常样本" meta={traces ? `最近 ${traces.length} 条授权 Trace 中 ${alerts.length} 条异常` : undefined} bodyClassName="p-0">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle px-4 py-3 text-[11px] text-text-muted">
            <span>仅列出失败、降级、部分成功和超时；不展示错误正文、用户输入或任意事件属性。</span>
            {lastTraceFetchAt !== null && <span>Trace 样本读取：{formatDateTime(lastTraceFetchAt)}</span>}
          </div>
          {initialLoading && !traces ? <AssetState>正在读取授权 Trace 样本…</AssetState>
            : traceError && !traces ? <ErrorState title="异常样本不可用" message="Trace 查询失败；此状态不代表没有告警。" onRetry={() => void load()} />
              : traceError ? <p role="alert" className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-[12px] text-amber-900">Trace 刷新失败，以下仍是上次成功读取的样本。</p>
                : null}
          {!initialLoading && traces && alerts.length === 0 && (
            <AssetState>{traceError ? '上次成功读取的授权 Trace 样本中没有失败、降级、部分成功或超时记录。' : '当前授权 Trace 样本中没有失败、降级、部分成功或超时记录。'}这不代表告警平台全局无告警。</AssetState>
          )}
          {alerts.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-left text-[12px]">
                <thead><tr className="border-b border-border-subtle text-[11px] text-text-muted"><th className="px-4 py-2 font-medium">时间</th><th className="px-4 py-2 font-medium">Run 结果</th><th className="px-4 py-2 font-medium">诊断码</th><th className="px-4 py-2 font-medium">Trace</th></tr></thead>
                <tbody>
                  {alerts.map((alert) => <tr key={alert.traceId} className="border-b border-border-subtle last:border-0 hover:bg-bg-elevated/50">
                    <td className="whitespace-nowrap px-4 py-3 tabular-nums text-text-secondary">{formatDateTime(Date.parse(alert.timestamp))}</td>
                    <td className="px-4 py-3"><span className={`inline-flex rounded border px-2 py-0.5 text-[11px] ${getRunStatusTone(alert.status)}`}>{getRunStatusLabel(alert.status)}</span></td>
                    <td className="px-4 py-3 font-mono text-text-secondary">{alert.code ?? '未记录'}</td>
                    <td className="px-4 py-3"><Link className="font-mono text-accent hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent" href={`/observability/traces/${encodeURIComponent(alert.traceId)}`}>{alert.traceId.slice(-12)}</Link></td>
                  </tr>)}
                </tbody>
              </table>
            </div>
          )}
        </AssetSection>
      </div>
    </AssetPageShell>
  )
}

export default function DegradationAlertsPage() {
  return <RoleGate minRole="admin" pageName="服务健康与告警"><SystemHealthContent /></RoleGate>
}
