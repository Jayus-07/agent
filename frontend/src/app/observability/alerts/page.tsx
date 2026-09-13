'use client'

/**
 * 系统告警 · 降级事件流 —— 回答"最近哪些能力在坏"。
 *
 * 数据源:
 *  - GET /api/observability/alerts        降级/告警事件（logs/degradation.jsonl 尾部）
 *  - GET /api/observability/skill-health  按节点/Skill 聚合的成功率/耗时/重试
 *
 * 注意与 /inventory 的"库存告警"（业务工单状态机）完全无关。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { authFetch } from '@/lib/authFetch'
import TraceBreadcrumb from '@/components/observability/trace/TraceBreadcrumb'

interface DegradationAlert {
  timestamp: string
  level: 'info' | 'warn' | 'error'
  code: string
  message: string
  detail: Record<string, unknown>
}

interface SkillHealth {
  name: string
  type: string
  total: number
  success: number
  error: number
  skipped: number
  retries: number
  avg_duration_ms: number
  success_rate: number | null
  last_status: string
  last_error: string
  last_ts: string
}

const LEVEL_STYLE: Record<string, string> = {
  error: 'bg-red-100 text-red-700',
  warn: 'bg-amber-100 text-amber-700',
  info: 'bg-sky-100 text-sky-700',
}

function formatTime(ts: string): string {
  if (!ts) return '--'
  return ts.replace('T', ' ').slice(0, 19)
}

export default function DegradationAlertsPage() {
  const [alerts, setAlerts] = useState<DegradationAlert[]>([])
  const [alertTotal, setAlertTotal] = useState(0)
  const [health, setHealth] = useState<SkillHealth[]>([])
  const [levelFilter, setLevelFilter] = useState<string>('')
  const [codeFilter, setCodeFilter] = useState<string>('')
  const [keyword, setKeyword] = useState('')
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadAlerts = useCallback(async () => {
    try {
      const resp = await authFetch('/api/observability/alerts?limit=300')
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const data = await resp.json()
      setAlerts(data.alerts || [])
      setAlertTotal(data.total || 0)
      setError('')
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  const loadHealth = useCallback(async () => {
    try {
      const resp = await authFetch('/api/observability/skill-health?limit=300')
      if (!resp.ok) return
      const data = await resp.json()
      setHealth(data.skills || [])
    } catch {
      // 健康卡片加载失败不阻塞告警列表
    }
  }, [])

  useEffect(() => {
    const init = async () => {
      setLoading(true)
      await Promise.all([loadAlerts(), loadHealth()])
      setLoading(false)
    }
    init()
  }, [loadAlerts, loadHealth])

  useEffect(() => {
    if (!autoRefresh) return
    const t = setInterval(loadAlerts, 30_000)
    return () => clearInterval(t)
  }, [autoRefresh, loadAlerts])

  const codeOptions = useMemo(
    () => Array.from(new Set(alerts.map((a) => a.code))).sort(),
    [alerts],
  )

  const filtered = useMemo(
    () =>
      alerts.filter((a) => {
        if (levelFilter && a.level !== levelFilter) return false
        if (codeFilter && a.code !== codeFilter) return false
        if (keyword) {
          const hay = `${a.code} ${a.message} ${JSON.stringify(a.detail || {})}`.toLowerCase()
          if (!hay.includes(keyword.toLowerCase())) return false
        }
        return true
      }),
    [alerts, levelFilter, codeFilter, keyword],
  )

  const errorCount = useMemo(() => alerts.filter((a) => a.level === 'error').length, [alerts])

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="max-w-[1440px] mx-auto px-6 py-6 space-y-5">
        <TraceBreadcrumb
          crumbs={[
            { label: '可观测中心', href: '/observability/traces' },
            { label: '系统告警 · 降级事件流' },
          ]}
        />

        {/* ── Header ── */}
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-3 flex-wrap">
            <Link href="/observability/traces" className="text-slate-400 hover:text-slate-600 text-sm">← 返回</Link>
            <h1 className="text-lg font-semibold text-slate-800">系统告警 · 降级事件流</h1>
            {errorCount > 0 && (
              <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-red-100 text-red-700">
                {errorCount} 条 error
              </span>
            )}
            <span className="text-[10px] text-slate-400">历史共 {alertTotal} 条</span>
          </div>
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-1.5 text-[11px] text-slate-500 cursor-pointer">
              <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} className="rounded" />
              30s 自动刷新
            </label>
            <button onClick={() => { loadAlerts(); loadHealth() }} className="text-xs text-slate-500 border border-slate-200 rounded px-3 py-1 hover:bg-slate-100">↻ 刷新</button>
          </div>
        </div>

        {/* ── 能力健康度卡片 ── */}
        <section>
          <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">能力健康度（最近 300 条 trace 聚合）</h2>
          {health.length === 0 ? (
            <div className="bg-white border border-slate-200 rounded-xl p-5 text-xs text-slate-400">暂无 span 数据</div>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3">
              {health.slice(0, 10).map((s) => {
                const rate = s.success_rate
                const rateColor =
                  rate === null ? 'bg-slate-100 text-slate-500'
                  : rate >= 0.95 ? 'bg-emerald-100 text-emerald-700'
                  : rate >= 0.8 ? 'bg-amber-100 text-amber-700'
                  : 'bg-red-100 text-red-700'
                return (
                  <div key={s.name} className="bg-white border border-slate-200 rounded-xl p-4 space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-semibold text-slate-700 truncate" title={s.name}>{s.name}</span>
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold ${rateColor}`}>
                        {rate === null ? '--' : `${(rate * 100).toFixed(0)}%`}
                      </span>
                    </div>
                    <div className="grid grid-cols-2 gap-x-2 gap-y-1 text-[10px] text-slate-500">
                      <span>调用 <b className="text-slate-700 font-mono">{s.total}</b></span>
                      <span>失败 <b className={s.error > 0 ? 'text-red-600 font-mono' : 'text-slate-700 font-mono'}>{s.error}</b></span>
                      <span>均值 <b className="text-slate-700 font-mono">{s.avg_duration_ms}ms</b></span>
                      <span>重试 <b className={s.retries > 0 ? 'text-orange-600 font-mono' : 'text-slate-700 font-mono'}>{s.retries}</b></span>
                    </div>
                    {s.last_error && (
                      <p className="text-[10px] text-red-500 truncate" title={s.last_error}>最近失败: {s.last_error}</p>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </section>

        {/* ── 筛选 ── */}
        <div className="flex items-center gap-3 flex-wrap">
          <select
            value={levelFilter}
            onChange={(e) => setLevelFilter(e.target.value)}
            className="text-xs border border-slate-200 rounded px-2 py-1.5 bg-white"
          >
            <option value="">全部级别</option>
            <option value="error">error</option>
            <option value="warn">warn</option>
            <option value="info">info</option>
          </select>
          <select
            value={codeFilter}
            onChange={(e) => setCodeFilter(e.target.value)}
            className="text-xs border border-slate-200 rounded px-2 py-1.5 bg-white max-w-[220px]"
          >
            <option value="">全部类型</option>
            {codeOptions.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="关键字（code / 消息 / detail）"
            className="text-xs border border-slate-200 rounded px-3 py-1.5 w-64 bg-white"
          />
          <span className="text-[10px] text-slate-400">{filtered.length}/{alerts.length} 条</span>
        </div>

        {/* ── 告警列表 ── */}
        <section className="bg-white border border-slate-200 rounded-xl divide-y divide-slate-100">
          {loading ? (
            <div className="p-8 text-center text-xs text-slate-400">加载中…</div>
          ) : error ? (
            <div className="p-8 text-center text-xs text-red-500">加载失败: {error}</div>
          ) : filtered.length === 0 ? (
            <div className="p-8 text-center text-xs text-slate-400">无匹配告警——系统最近没有降级事件 🎉</div>
          ) : (
            filtered.map((a, i) => (
              <div key={i} className="flex items-start gap-3 px-4 py-3 hover:bg-slate-50">
                <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold shrink-0 ${LEVEL_STYLE[a.level] || 'bg-slate-100 text-slate-600'}`}>
                  {a.level.toUpperCase()}
                </span>
                <div className="flex-1 min-w-0 space-y-0.5">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-xs font-semibold text-slate-700 font-mono">{a.code}</span>
                    <span className="text-xs text-slate-600">{a.message}</span>
                  </div>
                  {a.detail && Object.keys(a.detail).length > 0 && (
                    <p className="text-[10px] text-slate-400 font-mono truncate" title={JSON.stringify(a.detail)}>
                      {JSON.stringify(a.detail).slice(0, 180)}
                    </p>
                  )}
                </div>
                <span className="text-[10px] text-slate-400 font-mono shrink-0">{formatTime(a.timestamp)}</span>
              </div>
            ))
          )}
        </section>
      </div>
    </div>
  )
}
