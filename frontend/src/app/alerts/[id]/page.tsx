'use client'

import { useEffect, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { ArrowLeft, AlertCircle, AlertTriangle, CheckCircle2, XCircle, ExternalLink } from 'lucide-react'
import { alertService, type AlertDetail, type AlertEvent } from '@/services/alerts'
import { clsx } from 'clsx'

const EVENT_LABELS: Record<string, string> = {
  created: 'CREATE — 首次触发',
  upgraded: 'UPGRADE — 状态升级',
  reminded: 'REMIND — 持续提醒',
  resolved: 'RESOLVE — 已恢复',
  reopened: 'REOPEN — 重新激活',
  acknowledged: '确认告警',
  closed: '关闭告警',
}

export default function AlertDetailPage() {
  const { id } = useParams<{ id: string }>()
  const router = useRouter()
  const [detail, setDetail] = useState<AlertDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [acting, setActing] = useState(false)

  useEffect(() => {
    alertService.getAlert(Number(id)).then(d => {
      setDetail(d)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [id])

  async function handleAction(action: string) {
    setActing(true)
    try {
      if (action === 'acknowledged') {
        await alertService.patchAlert(Number(id), { status: 'acknowledged' })
      } else if (action === 'resolved') {
        await alertService.patchAlert(Number(id), { status: 'resolved', resolution_type: 'MANUAL_RESOLVED' })
      } else if (action === 'closed') {
        await alertService.patchAlert(Number(id), { status: 'closed', resolution_type: 'MANUAL_IGNORED' })
      }
      // refresh
      const d = await alertService.getAlert(Number(id))
      setDetail(d)
    } catch (e) {
      console.error('Action failed:', e)
    } finally {
      setActing(false)
    }
  }

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-sm text-text-muted">加载中...</p>
      </div>
    )
  }

  if (!detail) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-sm text-text-muted">告警未找到</p>
      </div>
    )
  }

  const c = detail.case

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <button onClick={() => router.back()} className="p-1.5 rounded-lg hover:bg-black/5 text-text-muted">
            <ArrowLeft size={18} />
          </button>
          <div>
            <h1 className="text-lg font-semibold text-text-primary">{c.product_id} · 库存预警</h1>
            <p className="text-xs text-text-muted">Case #{c.id} · 首次检测: {c.first_detected_at?.slice(0, 19)}</p>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-6">
          {/* Left: Timeline + Agent Analysis */}
          <div className="col-span-2 space-y-4">
            {/* Timeline */}
            <div className="bg-surface-base rounded-xl border border-border-subtle p-5">
              <h2 className="text-sm font-semibold text-text-primary mb-3">事件时间线</h2>
              <div className="space-y-3">
                {(detail.events || []).map((ev: AlertEvent, i: number) => (
                  <div key={ev.id || i} className="flex gap-3">
                    <div className="flex flex-col items-center">
                      <div className={clsx(
                        'w-2 h-2 rounded-full mt-1.5',
                        ev.notified ? 'bg-accent' : 'bg-text-muted'
                      )} />
                      {i < (detail.events || []).length - 1 && (
                        <div className="w-px flex-1 bg-border-subtle my-1" />
                      )}
                    </div>
                    <div className="flex-1 pb-2">
                      <div className="text-xs text-text-secondary">
                        {EVENT_LABELS[ev.event_type] || ev.event_type}
                      </div>
                      <div className="text-[11px] text-text-muted mt-0.5">
                        {ev.created_at?.slice(0, 19)}
                        {ev.from_state && ev.to_state && ` · ${ev.from_state} → ${ev.to_state}`}
                        {ev.notified && ' · 已通知'}
                      </div>
                      {ev.reason && ev.reason.length > 0 && (
                        <div className="text-[11px] text-text-muted mt-0.5">
                          {ev.reason.join('; ')}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
                {(!detail.events || detail.events.length === 0) && (
                  <p className="text-xs text-text-muted">暂无事件</p>
                )}
              </div>
            </div>

            {/* Agent Analysis placeholder */}
            <div className="bg-surface-base rounded-xl border border-border-subtle p-5">
              <h2 className="text-sm font-semibold text-text-primary mb-2">Agent 分析</h2>
              <p className="text-xs text-text-muted">
                库存状态: {c.current_state} · 级别: {c.current_level}
                {c.resolution_type ? ` · 解决方式: ${c.resolution_type}` : ''}
              </p>
            </div>
          </div>

          {/* Right: Ticket Operations */}
          <div className="space-y-4">
            <div className="bg-surface-base rounded-xl border border-border-subtle p-5">
              <h2 className="text-sm font-semibold text-text-primary mb-3">工单操作</h2>
              <div className="space-y-2 text-xs text-text-secondary mb-4">
                <div className="flex justify-between">
                  <span>状态</span>
                  <span className="font-medium">{STATUS_MAP[c.status] || c.status}</span>
                </div>
                <div className="flex justify-between">
                  <span>级别</span>
                  <span className={clsx(
                    c.current_level === 'critical' ? 'text-red-500' : c.current_level === 'warning' ? 'text-yellow-500' : 'text-blue-500'
                  )}>{c.current_level}</span>
                </div>
                <div className="flex justify-between">
                  <span>库存状态</span>
                  <span>{c.current_state}</span>
                </div>
              </div>

              {c.status === 'open' || c.status === 'acknowledged' ? (
                <div className="space-y-2">
                  {c.status !== 'acknowledged' && (
                    <button
                      onClick={() => handleAction('acknowledged')}
                      disabled={acting}
                      className="w-full py-2 text-xs rounded-lg bg-accent/5 text-accent hover:bg-accent/10 transition-colors disabled:opacity-50"
                    >
                      ✓ 确认告警
                    </button>
                  )}
                  <button
                    onClick={() => handleAction('resolved')}
                    disabled={acting}
                    className="w-full py-2 text-xs rounded-lg bg-green-50 text-green-700 hover:bg-green-100 transition-colors disabled:opacity-50"
                  >
                    🔧 已解决
                  </button>
                  <button
                    onClick={() => handleAction('closed')}
                    disabled={acting}
                    className="w-full py-2 text-xs rounded-lg bg-gray-50 text-text-muted hover:bg-gray-100 transition-colors disabled:opacity-50"
                  >
                    🚫 忽略
                  </button>
                </div>
              ) : c.status === 'resolved' ? (
                <p className="text-xs text-green-600">✅ 已解决 ({c.resolution_type})</p>
              ) : c.status === 'closed' ? (
                <p className="text-xs text-text-muted">🚫 已关闭</p>
              ) : null}
            </div>

            {/* Trace link */}
            <div className="bg-surface-base rounded-xl border border-border-subtle p-5">
              <h2 className="text-sm font-semibold text-text-primary mb-2">技术信息</h2>
              <p className="text-xs text-text-muted mb-2">
                Case ID: {c.id} · Product: {c.product_id}
              </p>
              <a
                href="/observability/traces"
                target="_blank"
                className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
              >
                查看 Trace <ExternalLink size={12} />
              </a>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

const STATUS_MAP: Record<string, string> = {
  open: 'OPEN',
  acknowledged: 'ACK',
  resolved: '已解决',
  closed: '已关闭',
}
