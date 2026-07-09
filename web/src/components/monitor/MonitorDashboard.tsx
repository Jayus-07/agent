'use client'

/**
 * MonitorDashboard — 精简版可观测性大盘
 * 纯 CSS + useEffect 轮询，零图表依赖。
 */

import { useState, useEffect, useCallback } from 'react'

// ── 类型 ──
interface PipelineMetrics {
  total_requests: number
  success: number; error: number; completed: number
  success_rate: number; active: number
  avg_elapsed_sec: number; p50_elapsed_sec: number
  p95_elapsed_sec: number; p99_elapsed_sec: number
}

interface ResourceData {
  cpu: { system_percent: number; cpu_count: number }
  memory: { rss_mb: number; system_percent: number; available_mb: number }
  uptime_seconds: number; request_count: number; warning_count: number
}

interface AlertItem { level: string; code: string; message: string }

export default function MonitorDashboard() {
  const [pipeline, setPipeline] = useState<PipelineMetrics | null>(null)
  const [res, setRes] = useState<ResourceData | null>(null)
  const [alerts, setAlerts] = useState<AlertItem[]>([])

  const poll = useCallback(async () => {
    try {
      const [m, r, a] = await Promise.all([
        fetch('/observability/metrics').then(r => r.json()).catch(() => null),
        fetch('/observability/resources').then(r => r.json()).catch(() => null),
        fetch('/observability/alerts?limit=20').then(r => r.json()).catch(() => ({ alerts: [] })),
      ])
      if (m?.pipeline) setPipeline(m.pipeline)
      if (r) setRes(r)
      setAlerts(a?.alerts || [])
    } catch { /* silent */ }
  }, [])

  useEffect(() => {
    poll()
    const id = setInterval(poll, 5000)
    return () => clearInterval(id)
  }, [poll])

  return (
    <div
      className="flex-1 overflow-auto p-4 md:p-6 space-y-4"
      style={{ backgroundColor: 'var(--surface-0)', color: 'var(--text)' }}
    >
      <h1 className="text-lg font-semibold">可观测性大盘</h1>

      {/* KPI 条 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard label="总请求" value={pipeline?.total_requests ?? '-'} color="var(--accent)" />
        <KpiCard label="成功率" value={pipeline != null ? `${(pipeline.success_rate * 100).toFixed(0)}%` : '-'} color="var(--success)" />
        <KpiCard label="P50" value={pipeline != null ? `${pipeline.p50_elapsed_sec}s` : '-'} color="var(--info)" />
        <KpiCard label="活跃" value={pipeline?.active ?? '-'} color="var(--warn)" />
      </div>

      {/* 双栏 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="rounded-xl p-4" style={{ backgroundColor: 'var(--surface-1)', border: '1px solid var(--border)' }}>
          <h3 className="text-sm font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>执行拓扑</h3>
          <div className="text-xs py-8 text-center" style={{ color: 'var(--text-muted)' }}>拓扑图已移除</div>
        </div>
        <div className="rounded-xl p-4 space-y-3" style={{ backgroundColor: 'var(--surface-1)', border: '1px solid var(--border)' }}>
          <h3 className="text-sm font-medium" style={{ color: 'var(--text-secondary)' }}>系统资源</h3>
          {res ? (
            <>
              <ProgressBar label="CPU" pct={res.cpu.system_percent} />
              <ProgressBar label="内存" pct={res.memory.system_percent} />
              <div className="text-xs space-y-1" style={{ color: 'var(--text-muted)' }}>
                <div>进程: {res.memory.rss_mb.toFixed(0)} MB | 可用: {res.memory.available_mb.toFixed(0)} MB</div>
                <div>核心: {res.cpu.cpu_count} | 请求: {res.request_count} | 告警: {res.warning_count}</div>
                <div>运行: {fmtUptime(res.uptime_seconds)}</div>
              </div>
            </>
          ) : (
            <div className="text-xs py-4 text-center" style={{ color: 'var(--text-muted)' }}>加载中...</div>
          )}
        </div>
      </div>

      {/* 延迟 + 告警 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="rounded-xl p-4 space-y-2" style={{ backgroundColor: 'var(--surface-1)', border: '1px solid var(--border)' }}>
          <h3 className="text-sm font-medium" style={{ color: 'var(--text-secondary)' }}>延迟分布</h3>
          {pipeline ? (
            [['P50', pipeline.p50_elapsed_sec], ['P95', pipeline.p95_elapsed_sec],
             ['P99', pipeline.p99_elapsed_sec], ['平均', pipeline.avg_elapsed_sec]].map(([l, v]) => (
              <div key={l} className="flex items-center gap-2 text-xs">
                <span className="w-8 text-right" style={{ color: 'var(--text-muted)' }}>{l}</span>
                <div className="flex-1 h-2 rounded-full" style={{ backgroundColor: 'var(--surface-0)' }}>
                  <div className="h-full rounded-full" style={{
                    width: `${Math.min(100, ((v as number) / Math.max(pipeline.p99_elapsed_sec, 1)) * 100)}%`,
                    backgroundColor: l === 'P99' ? 'var(--warn)' : 'var(--accent)',
                  }} />
                </div>
                <span className="w-12 text-right font-mono" style={{ color: 'var(--text)' }}>{(v as number)}s</span>
              </div>
            ))
          ) : (
            <div className="text-xs py-4 text-center" style={{ color: 'var(--text-muted)' }}>等待数据...</div>
          )}
        </div>

        <div className="rounded-xl p-4" style={{ backgroundColor: 'var(--surface-1)', border: '1px solid var(--border)' }}>
          <h3 className="text-sm font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>
            告警{alerts.length > 0 ? ` (${alerts.length})` : ''}
          </h3>
          {alerts.length === 0 ? (
            <div className="text-xs py-4 text-center" style={{ color: 'var(--text-muted)' }}>暂无告警</div>
          ) : (
            <div className="space-y-1 max-h-48 overflow-auto">
              {alerts.slice(0, 15).map((a, i) => (
                <div key={i} className="px-2 py-1.5 rounded text-xs"
                  style={{ backgroundColor: 'var(--surface-2)',
                    borderLeft: `3px solid ${a.level === 'error' ? 'var(--error)' : 'var(--warn)'}` }}>
                  <span style={{ color: a.level === 'error' ? 'var(--error)' : 'var(--warn)' }}>{a.code}</span>
                  <span className="ml-2" style={{ color: 'var(--text-muted)' }}>{a.message}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function KpiCard({ label, value, color }: { label: string; value: string | number; color: string }) {
  return (
    <div className="rounded-lg px-4 py-3 text-center"
      style={{ backgroundColor: 'var(--surface-1)', border: '1px solid var(--border)' }}>
      <div className="text-xl font-bold" style={{ color }}>{value}</div>
      <div className="text-[11px] mt-0.5" style={{ color: 'var(--text-muted)' }}>{label}</div>
    </div>
  )
}

function ProgressBar({ label, pct }: { label: string; pct: number }) {
  const color = pct > 80 ? 'var(--error)' : pct > 50 ? 'var(--warn)' : 'var(--success)'
  return (
    <div>
      <div className="flex justify-between text-[11px] mb-1">
        <span style={{ color: 'var(--text-muted)' }}>{label}</span>
        <span style={{ color: 'var(--text)' }}>{pct.toFixed(0)}%</span>
      </div>
      <div className="h-2 rounded-full" style={{ backgroundColor: 'var(--surface-0)' }}>
        <div className="h-full rounded-full transition-all duration-500"
          style={{ width: `${Math.min(100, pct)}%`, backgroundColor: color }} />
      </div>
    </div>
  )
}

function fmtUptime(s: number): string {
  if (s < 60) return `${Math.round(s)}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
}
