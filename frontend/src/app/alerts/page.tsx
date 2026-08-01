'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { AlertTriangle, AlertCircle, Info, CheckCircle2, ChevronRight, RefreshCw } from 'lucide-react'
import { alertService, type AlertCase, type AlertStats } from '@/services/alerts'
import { clsx } from 'clsx'

const LEVEL_CONFIG: Record<string, { icon: React.ReactNode; label: string; color: string }> = {
  critical: { icon: <AlertCircle size={16} />, label: '紧急', color: 'text-red-500 bg-red-50 border-red-200' },
  warning: { icon: <AlertTriangle size={16} />, label: '警告', color: 'text-yellow-500 bg-yellow-50 border-yellow-200' },
  info: { icon: <Info size={16} />, label: '提醒', color: 'text-blue-500 bg-blue-50 border-blue-200' },
}

const STATUS_MAP: Record<string, string> = {
  open: 'OPEN',
  acknowledged: 'ACK',
  resolved: '已解决',
  closed: '已关闭',
}

export default function AlertsPage() {
  const router = useRouter()
  const [stats, setStats] = useState<AlertStats>({ critical: 0, warning: 0, info: 0, resolved: 0 })
  const [alerts, setAlerts] = useState<AlertCase[]>([])
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<'active' | 'history'>('active')

  async function loadData() {
    setLoading(true)
    const [statsRes, alertsRes] = await Promise.all([
      alertService.getStats(),
      alertService.getAlerts({ status: tab === 'active' ? 'active' : 'history' }),
    ])
    setStats(statsRes.stats)
    setAlerts(alertsRes.cases || [])
    setLoading(false)
  }

  useEffect(() => { loadData() }, [tab])

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">告警中心</h1>
            <p className="text-xs text-text-muted mt-1">库存预警实时监控 · 状态机管理</p>
          </div>
          <button onClick={loadData} className="flex items-center gap-1.5 text-xs text-text-muted hover:text-text-primary transition-colors">
            <RefreshCw size={14} /> 刷新
          </button>
        </div>

        {/* Stats Cards */}
        <div className="grid grid-cols-4 gap-4 mb-6">
          {[
            { key: 'critical', label: '紧急', icon: <AlertCircle size={16} />, cls: 'border-red-200 bg-red-50' },
            { key: 'warning', label: '警告', icon: <AlertTriangle size={16} />, cls: 'border-yellow-200 bg-yellow-50' },
            { key: 'info', label: '提醒', icon: <Info size={16} />, cls: 'border-blue-200 bg-blue-50' },
            { key: 'resolved', label: '已解决', icon: <CheckCircle2 size={16} />, cls: 'border-green-200 bg-green-50' },
          ].map(s => (
            <div key={s.key} className={clsx('rounded-xl border p-4', s.cls)}>
              <div className="flex items-center gap-2 text-xs mb-1">{s.icon} {s.label}</div>
              <div className="text-2xl font-semibold text-text-primary">{stats[s.key as keyof AlertStats] ?? 0}</div>
            </div>
          ))}
        </div>

        {/* Tab + Filters */}
        <div className="flex items-center gap-4 mb-4">
          <div className="flex rounded-lg bg-black/5 p-0.5">
            <button
              onClick={() => setTab('active')}
              className={clsx('px-3 py-1.5 text-xs rounded-md transition-colors',
                tab === 'active' ? 'bg-white text-text-primary shadow-sm' : 'text-text-muted hover:text-text-secondary')}
            >
              ● 活跃告警
            </button>
            <button
              onClick={() => setTab('history')}
              className={clsx('px-3 py-1.5 text-xs rounded-md transition-colors',
                tab === 'history' ? 'bg-white text-text-primary shadow-sm' : 'text-text-muted hover:text-text-secondary')}
            >
              ○ 历史告警
            </button>
          </div>
        </div>

        {/* Alert List */}
        <div className="space-y-2">
          {loading && <p className="text-xs text-text-muted py-4">加载中...</p>}
          {!loading && alerts.length === 0 && (
            <p className="text-xs text-text-muted py-4">
              {tab === 'active' ? '当前无活跃告警 ✅' : '暂无历史告警'}
            </p>
          )}
          {alerts.map(a => {
            const levelCfg = LEVEL_CONFIG[a.current_level] || LEVEL_CONFIG.info
            return (
              <button
                key={a.id}
                onClick={() => router.push(`/alerts/${a.id}`)}
                className="w-full bg-surface-base rounded-xl border border-border-subtle p-4 hover:shadow-card transition-shadow text-left"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <span className={clsx('w-9 h-9 rounded-lg flex items-center justify-center border', levelCfg.color)}>
                      {levelCfg.icon}
                    </span>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-sm text-text-primary">{a.product_id}</span>
                        <span className={clsx('text-[10px] px-2 py-0.5 rounded-full', levelCfg.color)}>
                          {levelCfg.label}
                        </span>
                        <span className="text-[10px] bg-black/5 text-text-muted px-2 py-0.5 rounded-full">
                          {STATUS_MAP[a.status] || a.status}
                        </span>
                      </div>
                      <div className="text-xs text-text-muted mt-0.5">
                        当前库存: {a.current_state} · 检测时间: {a.first_detected_at?.slice(0, 10)}
                      </div>
                    </div>
                  </div>
                  <ChevronRight size={14} className="text-text-muted" />
                </div>
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}
