'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  ExternalLink,
  Info,
  Package,
  RefreshCw,
} from 'lucide-react'
import { alertService, type AlertCase, type AlertDetail, type AlertEvent, type AlertStats } from '@/api/alerts'
import {
  AssetActionButton,
  AssetPageShell,
  ASSET_SURFACE,
} from '@/components/layout/AssetPageShell'
import StatMetricBar, { type StatMetricItem } from '@/components/layout/StatMetricBar'
import DetailDrawer from '@/components/layout/DetailDrawer'
import EmptyState from '@/components/shared/EmptyState'
import ErrorState from '@/components/shared/ErrorState'
import { clsx } from 'clsx'

const LEVEL_CONFIG: Record<string, { icon: React.ReactNode; label: string; color: string; badgeCls: string }> = {
  critical: {
    icon: <AlertCircle size={16} />,
    label: '紧急',
    color: 'text-red-500 bg-red-50 border-red-200',
    badgeCls: 'bg-red-50 text-red-700 border border-red-200',
  },
  warning: {
    icon: <AlertTriangle size={16} />,
    label: '警告',
    color: 'text-amber-500 bg-amber-50 border-amber-200',
    badgeCls: 'bg-amber-50 text-amber-700 border border-amber-200',
  },
  info: {
    icon: <Info size={16} />,
    label: '提醒',
    color: 'text-blue-500 bg-blue-50 border-blue-200',
    badgeCls: 'bg-blue-50 text-blue-700 border border-blue-200',
  },
}

const STATUS_MAP: Record<string, string> = {
  open: '待处理',
  acknowledged: '已确认',
  resolved: '已解决',
  closed: '已关闭',
}

const EVENT_LABELS: Record<string, string> = {
  created: 'CREATE — 首次触发',
  upgraded: 'UPGRADE — 状态升级',
  reminded: 'REMIND — 持续提醒',
  resolved: 'RESOLVE — 已恢复',
  reopened: 'REOPEN — 重新激活',
  acknowledged: '确认告警',
  closed: '关闭告警',
}

export default function AlertsPage() {
  const router = useRouter()
  const [stats, setStats] = useState<AlertStats>({ critical: 0, warning: 0, info: 0, resolved: 0 })
  const [alerts, setAlerts] = useState<AlertCase[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<'active' | 'history'>('active')

  // 详情抽屉状态
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detail, setDetail] = useState<AlertDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [acting, setActing] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  async function loadData() {
    setLoading(true)
    setError(null)
    try {
      const [statsRes, alertsRes] = await Promise.all([
        alertService.getStats(),
        alertService.getAlerts({ status: tab }),
      ])
      setStats(statsRes.stats)
      setAlerts(alertsRes.cases)
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载告警失败')
      setAlerts([])
    } finally {
      setLoading(false)
    }
  }

  async function openDrawer(id: number) {
    setSelectedId(id)
    setDetailLoading(true)
    setActionError(null)
    try {
      const d = await alertService.getAlert(id)
      setDetail(d)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : '加载工单详情失败')
    } finally {
      setDetailLoading(false)
    }
  }

  async function handleAction(action: string) {
    if (!selectedId) return
    setActing(true)
    setActionError(null)
    try {
      if (action === 'acknowledged') {
        await alertService.patchAlert(selectedId, { status: 'acknowledged' })
      } else if (action === 'resolved') {
        await alertService.patchAlert(selectedId, { status: 'resolved', resolution_type: 'MANUAL_RESOLVED' })
      } else if (action === 'closed') {
        await alertService.patchAlert(selectedId, { status: 'closed', resolution_type: 'MANUAL_IGNORED' })
      }
      const [updatedDetail] = await Promise.all([
        alertService.getAlert(selectedId),
        loadData(),
      ])
      setDetail(updatedDetail)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : '操作失败')
    } finally {
      setActing(false)
    }
  }

  useEffect(() => {
    void loadData()
  }, [tab])

  const metricItems: StatMetricItem[] = [
    {
      label: '紧急告警',
      value: stats.critical,
      tone: stats.critical > 0 ? 'danger' : 'default',
      hint: '需立即处置',
    },
    {
      label: '警告事件',
      value: stats.warning,
      tone: stats.warning > 0 ? 'warn' : 'default',
      hint: '水位偏低预警',
    },
    {
      label: '提醒跟踪',
      value: stats.info,
      tone: 'default',
      hint: '常规监控水位',
    },
    {
      label: '已解决工单',
      value: stats.resolved,
      tone: 'default',
      hint: '恢复/归档数量',
    },
  ]

  return (
    <AssetPageShell
      title="商品库存预警"
      desc="选品与商城业务层 · 实时库存水位与告警工单状态机"
      actions={
        <AssetActionButton
          icon={<RefreshCw size={14} className={loading ? 'animate-spin' : ''} />}
          onClick={loadData}
          disabled={loading}
        >
          刷新
        </AssetActionButton>
      }
    >
      <div className="space-y-6">
        {/* 指标条 */}
        <StatMetricBar items={metricItems} />

        {/* 筛选与 Tab */}
        <div className="flex items-center justify-between border-b border-border-subtle pb-3">
          <div className="flex items-center gap-1 rounded-lg bg-black/5 p-1">
            <button
              type="button"
              onClick={() => setTab('active')}
              className={clsx(
                'rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
                tab === 'active'
                  ? 'bg-white text-text-primary shadow-sm'
                  : 'text-text-muted hover:text-text-primary',
              )}
            >
              ● 活跃告警
            </button>
            <button
              type="button"
              onClick={() => setTab('history')}
              className={clsx(
                'rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
                tab === 'history'
                  ? 'bg-white text-text-primary shadow-sm'
                  : 'text-text-muted hover:text-text-primary',
              )}
            >
              ○ 历史告警
            </button>
          </div>
          <span className="text-xs text-text-muted">共 {alerts.length} 个工单</span>
        </div>

        {/* 列表区 */}
        {loading && (
          <div className="space-y-3">
            {[1, 2, 3].map((i) => (
              <div
                key={i}
                className={clsx(ASSET_SURFACE, 'h-20 animate-pulse bg-black/[0.02] p-4')}
              />
            ))}
          </div>
        )}

        {!loading && error && (
          <ErrorState title="加载库存告警失败" message={error} onRetry={loadData} />
        )}

        {!loading && !error && alerts.length === 0 && (
          <EmptyState
            title={tab === 'active' ? '活跃库存告警' : '历史库存告警'}
            description={
              tab === 'active'
                ? '全部商品库存水位正常，未检测到欠损或超卖风险。'
                : '暂无已归档或已关闭的告警记录。'
            }
            onAction={loadData}
            actionLabel="刷新数据"
          />
        )}

        {!loading && !error && alerts.length > 0 && (
          <div className="space-y-2.5">
            {alerts.map((a) => {
              const levelCfg = LEVEL_CONFIG[a.current_level] || LEVEL_CONFIG.info
              return (
                <button
                  key={a.id}
                  type="button"
                  onClick={() => openDrawer(a.id)}
                  className={clsx(
                    ASSET_SURFACE,
                    'w-full p-4 text-left transition-all hover:border-accent/40 hover:shadow-md',
                  )}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3.5">
                      <span
                        className={clsx(
                          'flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border',
                          levelCfg.color,
                        )}
                      >
                        {levelCfg.icon}
                      </span>
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="text-[14px] font-semibold text-text-primary">
                            {a.product_id}
                          </span>
                          <span className={clsx('rounded-full px-2 py-0.5 text-[11px] font-medium', levelCfg.badgeCls)}>
                            {levelCfg.label}
                          </span>
                          <span className="rounded-full bg-black/5 px-2 py-0.5 text-[11px] text-text-muted">
                            {STATUS_MAP[a.status] || a.status}
                          </span>
                        </div>
                        <div className="mt-1 text-xs text-text-muted">
                          当前库存状态: <span className="font-medium text-text-secondary">{a.current_state}</span>
                          {' · '}首次触发: {a.first_detected_at?.slice(0, 16) || '-'}
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 text-text-muted">
                      <span className="text-[12px]">处理工单</span>
                      <ChevronRight size={15} />
                    </div>
                  </div>
                </button>
              )
            })}
          </div>
        )}
      </div>

      {/* 统一右侧详情抽屉 */}
      <DetailDrawer
        open={Boolean(selectedId)}
        onClose={() => {
          setSelectedId(null)
          setDetail(null)
        }}
        title={detail ? `${detail.case.product_id} · 库存预警工单` : '工单详情'}
        subtitle={detail ? `Case #${detail.case.id} · 首次检测: ${detail.case.first_detected_at?.slice(0, 19)}` : undefined}
        actions={
          detail && (
            <button
              type="button"
              onClick={() => router.push(`/alerts/${detail.case.id}`)}
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-text-muted transition-colors hover:bg-black/5 hover:text-text-primary"
              title="打开独立详情页"
            >
              独立页面 <ExternalLink size={12} />
            </button>
          )
        }
      >
        {detailLoading && (
          <div className="space-y-4 py-6">
            <div className="h-8 animate-pulse rounded-lg bg-black/5" />
            <div className="h-32 animate-pulse rounded-lg bg-black/5" />
          </div>
        )}

        {!detailLoading && detail && (
          <div className="space-y-5">
            {/* 核心状态与快捷操作 */}
            <div className={clsx(ASSET_SURFACE, 'p-4')}>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-text-muted">工单处置</h3>
              <div className="mt-3 grid grid-cols-3 gap-2 border-b border-border-subtle pb-3 text-xs">
                <div>
                  <span className="text-text-muted">工单状态：</span>
                  <span className="font-semibold text-text-primary">
                    {STATUS_MAP[detail.case.status] || detail.case.status}
                  </span>
                </div>
                <div>
                  <span className="text-text-muted">预警级别：</span>
                  <span className="font-semibold text-text-primary">
                    {detail.case.current_level}
                  </span>
                </div>
                <div>
                  <span className="text-text-muted">当前水位：</span>
                  <span className="font-semibold text-text-primary">
                    {detail.case.current_state}
                  </span>
                </div>
              </div>

              {/* 动作区 */}
              {(detail.case.status === 'open' || detail.case.status === 'acknowledged') && (
                <div className="mt-3.5 flex flex-wrap gap-2">
                  {detail.case.status !== 'acknowledged' && (
                    <button
                      type="button"
                      onClick={() => handleAction('acknowledged')}
                      disabled={acting}
                      className="rounded-lg bg-accent/10 px-3 py-1.5 text-xs font-medium text-accent transition-colors hover:bg-accent/20 disabled:opacity-50"
                    >
                      ✓ 确认预警
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => handleAction('resolved')}
                    disabled={acting}
                    className="rounded-lg bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700 transition-colors hover:bg-emerald-100 disabled:opacity-50"
                  >
                    🔧 标记已解决
                  </button>
                  <button
                    type="button"
                    onClick={() => handleAction('closed')}
                    disabled={acting}
                    className="rounded-lg bg-gray-100 px-3 py-1.5 text-xs font-medium text-text-secondary transition-colors hover:bg-gray-200 disabled:opacity-50"
                  >
                    🚫 忽略关闭
                  </button>
                </div>
              )}

              {detail.case.status === 'resolved' && (
                <div className="mt-3 flex items-center gap-1.5 text-xs text-emerald-600">
                  <CheckCircle2 size={14} /> 已解决 ({detail.case.resolution_type || '手动'})
                </div>
              )}

              {detail.case.status === 'closed' && (
                <div className="mt-3 text-xs text-text-muted">🚫 已关闭</div>
              )}

              {actionError && (
                <p className="mt-2 text-xs text-rose-500">操作失败：{actionError}</p>
              )}
            </div>

            {/* 事件时间线 */}
            <div className={clsx(ASSET_SURFACE, 'p-4')}>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-text-muted">事件时间线</h3>
              <div className="mt-3 space-y-3">
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
                      <div className="text-xs font-medium text-text-primary">
                        {EVENT_LABELS[ev.event_type] || ev.event_type}
                      </div>
                      <div className="mt-0.5 text-[11px] text-text-muted">
                        {ev.created_at?.slice(0, 19)}
                        {ev.from_state && ev.to_state && ` · ${ev.from_state} → ${ev.to_state}`}
                        {ev.notified && ' · 已下发通知'}
                      </div>
                      {ev.reason && ev.reason.length > 0 && (
                        <div className="mt-0.5 text-[11px] text-text-muted">
                          原因: {ev.reason.join('; ')}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
                {(!detail.events || detail.events.length === 0) && (
                  <p className="py-2 text-xs text-text-muted">暂无流转事件</p>
                )}
              </div>
            </div>

            {/* 技术溯源 */}
            <div className={clsx(ASSET_SURFACE, 'p-4')}>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-text-muted">关联技术溯源</h3>
              <div className="mt-2 text-xs text-text-muted">
                Case ID: <code className="rounded bg-black/5 px-1 py-0.5 font-mono">{detail.case.id}</code>
              </div>
              <div className="mt-2">
                <a
                  href="/observability/monitoring"
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
                >
                  在运行控制中心查看 Trace <ExternalLink size={12} />
                </a>
              </div>
            </div>
          </div>
        )}
      </DetailDrawer>
    </AssetPageShell>
  )
}
