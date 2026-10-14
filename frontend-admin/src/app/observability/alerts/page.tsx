'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { RefreshCw } from 'lucide-react'
import {
  getDegradationAlerts,
  getSkillHealth,
  getSystemHealth,
  listTraces,
  type DegradationAlertsResponse,
  type SkillHealthResponse,
  type SystemHealthSnapshot,
} from '@/api/observability'
import { AssetActionButton, AssetPageShell, AssetSection, AssetState } from '@/components/layout/AssetPageShell'
import RoleGate from '@/components/auth/RoleGate'
import ErrorState from '@/components/shared/ErrorState'
import {
  buildDegradationAlertSample,
  buildRuntimeAlertSample,
  buildSkillHealthRows,
  buildSystemHealthRows,
  getHealthSnapshotFreshness,
  type HealthProbeState,
  type SkillHealthRow,
} from '@/lib/observability/serviceHealth'
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

const ALERT_LEVEL_LABEL = { info: '信息', warn: '警告', error: '错误', unknown: '未分级' }
const ALERT_LEVEL_TONE = {
  info: 'border-sky-200 bg-sky-50 text-sky-800',
  warn: 'border-amber-200 bg-amber-50 text-amber-800',
  error: 'border-red-200 bg-red-50 text-red-800',
  unknown: 'border-slate-200 bg-slate-50 text-slate-600',
}
const SKILL_ERROR_KIND_LABEL: Record<NonNullable<SkillHealthRow['lastErrorKind']>, string> = {
  denied: '策略拦截',
  not_configured: '未配置',
  failure: '故障',
}

function formatDateTime(timestampMs: number): string {
  if (!Number.isFinite(timestampMs)) return '未采集'
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'medium', hour12: false }).format(timestampMs)
}

function SystemHealthContent() {
  const [health, setHealth] = useState<SystemHealthSnapshot | null>(null)
  const [traces, setTraces] = useState<TraceRecord[] | null>(null)
  const [degradation, setDegradation] = useState<DegradationAlertsResponse | null>(null)
  const [skillHealth, setSkillHealth] = useState<SkillHealthResponse | null>(null)
  const [healthError, setHealthError] = useState(false)
  const [traceError, setTraceError] = useState(false)
  const [degradationError, setDegradationError] = useState(false)
  const [skillHealthError, setSkillHealthError] = useState(false)
  const [degradationLevelFilter, setDegradationLevelFilter] = useState('all')
  const [degradationCodeFilter, setDegradationCodeFilter] = useState('all')
  const [initialLoading, setInitialLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [lastTraceFetchAt, setLastTraceFetchAt] = useState<number | null>(null)
  const requestSequence = useRef(0)

  const load = useCallback(async () => {
    const sequence = ++requestSequence.current
    setRefreshing(true)
    const [healthResult, traceResult, degradationResult, skillHealthResult] = await Promise.allSettled([
      getSystemHealth(),
      listTraces(200),
      getDegradationAlerts(300),
      getSkillHealth(300),
    ])
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
    if (degradationResult.status === 'fulfilled') {
      setDegradation(degradationResult.value)
      setDegradationError(false)
    } else {
      setDegradationError(true)
    }
    if (skillHealthResult.status === 'fulfilled') {
      setSkillHealth(skillHealthResult.value)
      setSkillHealthError(false)
    } else {
      setSkillHealthError(true)
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
  const degradationRows = useMemo(
    () => buildDegradationAlertSample(degradation?.alerts ?? []),
    [degradation],
  )
  const skillRows = useMemo(
    () => buildSkillHealthRows(skillHealth?.skills ?? []),
    [skillHealth],
  )
  const degradationCodes = useMemo(
    () => Array.from(new Set(degradationRows.map((event) => event.code).filter((code): code is string => code !== null))).sort(),
    [degradationRows],
  )
  const filteredDegradationRows = useMemo(
    () => degradationRows.filter((event) => {
      if (degradationLevelFilter !== 'all' && event.level !== degradationLevelFilter) return false
      if (degradationCodeFilter === '__missing' && event.code !== null) return false
      if (degradationCodeFilter !== 'all' && degradationCodeFilter !== '__missing' && event.code !== degradationCodeFilter) return false
      return true
    }),
    [degradationRows, degradationLevelFilter, degradationCodeFilter],
  )
  const rows = useMemo(() => health ? buildSystemHealthRows(health) : [], [health])
  const freshness = health ? getHealthSnapshotFreshness(health) : 'unknown'
  const snapshotTime = health?.collected_at ? formatDateTime(health.collected_at * 1000) : '未返回采集时间'

  return (
    <AssetPageShell
      title="服务健康与告警"
      desc="健康状态来自受保护的服务快照；降级事件、授权 Trace 异常样本和 Skill 统计分别沿用现有数据源。"
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

        <AssetSection
          title="降级事件流"
          meta={degradation ? '历史共 ' + degradation.total + ' 条 / 当前读取 ' + degradation.alerts.length + ' 条' : undefined}
          bodyClassName="p-0"
        >
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle px-4 py-3 text-[11px] text-text-muted">
            <span>来源：受管理员保护的降级事件日志；仅展示时间、级别和安全诊断码。</span>
            <div className="flex flex-wrap gap-2">
              <label className="flex items-center gap-2">
                <span>级别</span>
                <select
                  aria-label="按级别筛选降级事件"
                  className="rounded border border-border-subtle bg-surface-base px-2 py-1 text-text-primary"
                  value={degradationLevelFilter}
                  onChange={(event) => setDegradationLevelFilter(event.target.value)}
                >
                  <option value="all">全部</option>
                  <option value="info">信息</option>
                  <option value="warn">警告</option>
                  <option value="error">错误</option>
                  <option value="unknown">未分级</option>
                </select>
              </label>
              <label className="flex items-center gap-2">
                <span>诊断码</span>
                <select
                  aria-label="按诊断码筛选降级事件"
                  className="rounded border border-border-subtle bg-surface-base px-2 py-1 text-text-primary"
                  value={degradationCodeFilter}
                  onChange={(event) => setDegradationCodeFilter(event.target.value)}
                >
                  <option value="all">全部</option>
                  <option value="__missing">未记录</option>
                  {degradationCodes.map((code) => <option key={code} value={code}>{code}</option>)}
                </select>
              </label>
            </div>
          </div>
          {initialLoading && !degradation ? <AssetState>正在读取降级事件…</AssetState>
            : degradationError && !degradation ? <ErrorState title="降级事件不可用" message="无法读取降级事件日志；此状态不代表没有降级事件。" onRetry={() => void load()} />
              : degradationError ? <p role="alert" className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-[12px] text-amber-900">降级事件刷新失败，以下仍是上次成功读取的数据。</p>
                : null}
          {!initialLoading && degradation && filteredDegradationRows.length === 0 && (
            <AssetState>{degradationError ? '上次成功读取的数据中没有匹配事件。' : '当前读取的降级事件中没有匹配记录。'}</AssetState>
          )}
          {filteredDegradationRows.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[600px] text-left text-[12px]">
                <thead><tr className="border-b border-border-subtle text-[11px] text-text-muted"><th className="px-4 py-2 font-medium">时间</th><th className="px-4 py-2 font-medium">级别</th><th className="px-4 py-2 font-medium">诊断码</th></tr></thead>
                <tbody>
                  {filteredDegradationRows.map((event, index) => (
                    <tr key={String(index) + ':' + (event.timestamp ?? '') + ':' + (event.code ?? '')} className="border-b border-border-subtle last:border-0">
                      <td className="whitespace-nowrap px-4 py-3 tabular-nums text-text-secondary">{event.timestamp ? formatDateTime(Date.parse(event.timestamp)) : '未采集'}</td>
                      <td className="px-4 py-3"><span className={'inline-flex rounded border px-2 py-0.5 text-[11px] ' + ALERT_LEVEL_TONE[event.level]}>{ALERT_LEVEL_LABEL[event.level]}</span></td>
                      <td className="px-4 py-3 font-mono text-text-secondary">{event.code ?? '未记录'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </AssetSection>

        <AssetSection
          title="Skill 健康统计"
          meta={skillHealth ? '最近 ' + skillHealth.trace_window + ' 条 Trace 的 Span 聚合' : undefined}
          bodyClassName="p-0"
        >
          <div className="border-b border-border-subtle px-4 py-3 text-[11px] text-text-muted">
            来源：现有 Skill 健康统计接口；展示调用量、成功率、平均耗时、重试和脱敏错误分类。
          </div>
          {initialLoading && !skillHealth ? <AssetState>正在读取 Skill 健康统计…</AssetState>
            : skillHealthError && !skillHealth ? <ErrorState title="Skill 健康统计不可用" message="无法读取 Skill 统计；此状态不代表 Skill 没有故障。" onRetry={() => void load()} />
              : skillHealthError ? <p role="alert" className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-[12px] text-amber-900">Skill 统计刷新失败，以下仍是上次成功读取的数据。</p>
                : null}
          {!initialLoading && skillHealth && skillRows.length === 0 && <AssetState>当前统计窗口中没有 Skill Span。</AssetState>}
          {skillRows.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[780px] text-left text-[12px]">
                <thead><tr className="border-b border-border-subtle text-[11px] text-text-muted"><th className="px-4 py-2 font-medium">Skill / 节点</th><th className="px-4 py-2 font-medium">类型</th><th className="px-4 py-2 font-medium">调用</th><th className="px-4 py-2 font-medium">成功率</th><th className="px-4 py-2 font-medium">平均耗时</th><th className="px-4 py-2 font-medium">重试</th><th className="px-4 py-2 font-medium">真实故障 / 中性</th><th className="px-4 py-2 font-medium">最近错误分类</th></tr></thead>
                <tbody>
                  {skillRows.map((skill) => (
                    <tr key={skill.name} className="border-b border-border-subtle last:border-0">
                      <th scope="row" className="px-4 py-3 font-medium text-text-primary">{skill.name}</th>
                      <td className="px-4 py-3 text-text-secondary">{skill.type}</td>
                      <td className="px-4 py-3 tabular-nums text-text-secondary">{skill.total}</td>
                      <td className="px-4 py-3 tabular-nums text-text-secondary">{skill.successRate === null ? '样本不足' : Math.round(skill.successRate * 100) + '%'}</td>
                      <td className="px-4 py-3 tabular-nums text-text-secondary">{skill.averageDurationMs} ms</td>
                      <td className="px-4 py-3 tabular-nums text-text-secondary">{skill.retries}</td>
                      <td className="px-4 py-3 tabular-nums text-text-secondary">{skill.realErrors} / {skill.neutral}</td>
                      <td className="px-4 py-3 text-text-secondary">{skill.lastErrorKind ? SKILL_ERROR_KIND_LABEL[skill.lastErrorKind] : '无'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
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
