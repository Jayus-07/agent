'use client'

import { useEffect, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import {
  ArrowLeft,
  CheckCircle2,
  ExternalLink,
} from 'lucide-react'
import { alertService, type AlertDetail, type AlertEvent } from '@/api/alerts'
import {
  AssetActionButton,
  AssetPageShell,
  ASSET_SURFACE,
} from '@/components/layout/AssetPageShell'
import ErrorState from '@/components/shared/ErrorState'
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

const STATUS_MAP: Record<string, string> = {
  open: '待处理',
  acknowledged: '已确认',
  resolved: '已解决',
  closed: '已关闭',
}

export default function AlertDetailPage() {
  const { id } = useParams<{ id: string }>()
  const router = useRouter()
  const [detail, setDetail] = useState<AlertDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [acting, setActing] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  async function loadDetail() {
    setLoading(true)
    setError(null)
    try {
      const d = await alertService.getAlert(Number(id))
      setDetail(d)
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载工单详情失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadDetail()
  }, [id])

  async function handleAction(action: string) {
    setActing(true)
    setActionError(null)
    try {
      if (action === 'acknowledged') {
        await alertService.patchAlert(Number(id), { status: 'acknowledged' })
      } else if (action === 'resolved') {
        await alertService.patchAlert(Number(id), { status: 'resolved', resolution_type: 'MANUAL_RESOLVED' })
      } else if (action === 'closed') {
        await alertService.patchAlert(Number(id), { status: 'closed', resolution_type: 'MANUAL_IGNORED' })
      }
      await loadDetail()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : '操作失败')
    } finally {
      setActing(false)
    }
  }

  if (loading) {
    return (
      <AssetPageShell title="商品库存预警详情">
        <div className="space-y-4 py-8">
          <div className="h-10 animate-pulse rounded-lg bg-black/5" />
          <div className="h-48 animate-pulse rounded-lg bg-black/5" />
        </div>
      </AssetPageShell>
    )
  }

  if (error || !detail) {
    return (
      <AssetPageShell
        title="商品库存预警详情"
        actions={
          <AssetActionButton icon={<ArrowLeft size={14} />} onClick={() => router.push('/alerts')}>
            返回列表
          </AssetActionButton>
        }
      >
        <ErrorState title="工单加载失败" message={error || '未找到该告警工单'} onRetry={loadDetail} />
      </AssetPageShell>
    )
  }

  const c = detail.case

  return (
    <AssetPageShell
      title={`${c.product_id} · 库存预警工单`}
      desc={`Case #${c.id} · 首次检测: ${c.first_detected_at?.slice(0, 19) || '-'}`}
      actions={
        <AssetActionButton icon={<ArrowLeft size={14} />} onClick={() => router.push('/alerts')}>
          返回列表
        </AssetActionButton>
      }
    >
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* 左侧：事件时间线与分析 */}
        <div className="space-y-5 lg:col-span-2">
          {/* 事件时间线 */}
          <div className={clsx(ASSET_SURFACE, 'p-5')}>
            <h2 className="text-sm font-semibold text-text-primary">事件流转时间线</h2>
            <div className="mt-4 space-y-3.5">
              {(detail.events || []).map((ev: AlertEvent, i: number) => (
                <div key={ev.id || i} className="flex gap-3">
                  <div className="flex flex-col items-center">
                    <div
                      className={clsx(
                        'mt-1.5 h-2 w-2 rounded-full',
                        ev.notified ? 'bg-accent' : 'bg-text-muted',
                      )}
                    />
                    {i < (detail.events || []).length - 1 && (
                      <div className="my-1 w-px flex-1 bg-border-subtle" />
                    )}
                  </div>
                  <div className="flex-1 pb-1">
                    <div className="text-xs font-semibold text-text-primary">
                      {EVENT_LABELS[ev.event_type] || ev.event_type}
                    </div>
                    <div className="mt-0.5 text-[11px] text-text-muted">
                      {ev.created_at?.slice(0, 19)}
                      {ev.from_state && ev.to_state && ` · 状态变迁: ${ev.from_state} → ${ev.to_state}`}
                      {ev.notified && ' · 已下发通知渠道'}
                    </div>
                    {ev.reason && ev.reason.length > 0 && (
                      <div className="mt-1 text-[11px] text-text-secondary">
                        规则触发原因: {ev.reason.join('; ')}
                      </div>
                    )}
                  </div>
                </div>
              ))}
              {(!detail.events || detail.events.length === 0) && (
                <p className="py-2 text-xs text-text-muted">暂无事件记录</p>
              )}
            </div>
          </div>

          {/* Agent 分析 */}
          <div className={clsx(ASSET_SURFACE, 'p-5')}>
            <h2 className="text-sm font-semibold text-text-primary">Agent 状态机分析</h2>
            <div className="mt-3 text-xs leading-relaxed text-text-secondary">
              <p>当前库存状态为 <span className="font-semibold text-text-primary">{c.current_state}</span>，预警等级判定为 <span className="font-semibold text-text-primary">{c.current_level}</span>。</p>
              {c.resolution_type && <p className="mt-1">解决处置方式：{c.resolution_type}</p>}
            </div>
          </div>
        </div>

        {/* 右侧：工单操作与技术信息 */}
        <div className="space-y-5">
          <div className={clsx(ASSET_SURFACE, 'p-5')}>
            <h2 className="text-sm font-semibold text-text-primary">工单处理</h2>
            <div className="mt-3 space-y-2.5 border-b border-border-subtle pb-4 text-xs">
              <div className="flex justify-between">
                <span className="text-text-muted">当前状态</span>
                <span className="font-medium text-text-primary">{STATUS_MAP[c.status] || c.status}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-muted">预警级别</span>
                <span className="font-medium text-text-primary">{c.current_level}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-muted">库存水位</span>
                <span className="font-medium text-text-primary">{c.current_state}</span>
              </div>
            </div>

            {(c.status === 'open' || c.status === 'acknowledged') ? (
              <div className="mt-4 space-y-2">
                {c.status !== 'acknowledged' && (
                  <button
                    type="button"
                    onClick={() => handleAction('acknowledged')}
                    disabled={acting}
                    className="w-full rounded-lg bg-accent/10 py-2 text-xs font-medium text-accent transition-colors hover:bg-accent/20 disabled:opacity-50"
                  >
                    ✓ 确认告警
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => handleAction('resolved')}
                  disabled={acting}
                  className="w-full rounded-lg bg-emerald-50 py-2 text-xs font-medium text-emerald-700 transition-colors hover:bg-emerald-100 disabled:opacity-50"
                >
                  🔧 标记已解决
                </button>
                <button
                  type="button"
                  onClick={() => handleAction('closed')}
                  disabled={acting}
                  className="w-full rounded-lg bg-gray-100 py-2 text-xs font-medium text-text-secondary transition-colors hover:bg-gray-200 disabled:opacity-50"
                >
                  🚫 忽略关闭
                </button>
              </div>
            ) : c.status === 'resolved' ? (
              <div className="mt-4 flex items-center gap-1.5 text-xs text-emerald-600">
                <CheckCircle2 size={14} /> 已解决 ({c.resolution_type || '手动'})
              </div>
            ) : c.status === 'closed' ? (
              <div className="mt-4 text-xs text-text-muted">🚫 已关闭</div>
            ) : null}

            {actionError && (
              <p className="mt-2 text-xs text-rose-500">操作失败：{actionError}</p>
            )}
          </div>

          <div className={clsx(ASSET_SURFACE, 'p-5')}>
            <h2 className="text-sm font-semibold text-text-primary">技术信息</h2>
            <div className="mt-2 space-y-1 text-xs text-text-muted">
              <div>Case ID: <code className="font-mono">{c.id}</code></div>
              <div>商品编码: <code className="font-mono">{c.product_id}</code></div>
            </div>
            <div className="mt-3">
              <a
                href="/observability/monitoring"
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
              >
                在运行控制中心查看关联 Trace <ExternalLink size={12} />
              </a>
            </div>
          </div>
        </div>
      </div>
    </AssetPageShell>
  )
}
