'use client'

/**
 * /alerts — 我的告警（只读视图，UX P1 ⑤ / X7 修复，2026-09-17）
 *
 * ADR-001：告警归 (workspace) 单组——管理端从 AdminShell nav 进入同一业务，
 * 页内按角色切数据范围。用户端定位为「值班查看」：
 * - 只读：统计卡 + 进行中工单列表 + 级别徽章（critical 红 / warning 琥珀 / info 灰）
 * - 工单流转（acknowledged/resolved/closed）留在管理端 frontend-admin /alerts，
 *   此处不提供操作按钮，也不写跨端假链接
 * - 错误反馈走 ErrorCard（三段式，X3）；空态给引导（§4.6）
 *
 * 数据源：alertService（库存告警工单 /api/inventory/*）——
 * 注意与 /observability/alerts（系统降级日志）无关。
 */

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { AlertTriangle, Package } from 'lucide-react'
import { alertService, type AlertCase, type AlertStats } from '@/api/alerts'
import ErrorCard from '@/components/shared/ErrorCard'

const LEVEL_BADGE: Record<string, string> = {
  critical: 'bg-red-100 text-red-700',
  warning: 'bg-amber-100 text-amber-700',
  info: 'bg-gray-100 text-gray-600',
}

const LEVEL_LABEL: Record<string, string> = {
  critical: '严重',
  warning: '警告',
  info: '提示',
}

const STATUS_LABEL: Record<string, string> = {
  open: '待确认',
  acknowledged: '已确认',
  resolved: '已解决',
  closed: '已关闭',
}

const STATE_LABEL: Record<string, string> = {
  low: '低库存',
  out: '缺货',
  overstock: '积压',
}

function badge(cls: string | undefined) {
  return cls ?? 'bg-gray-100 text-gray-600'
}

export default function MyAlertsPage() {
  const [stats, setStats] = useState<AlertStats | null>(null)
  const [cases, setCases] = useState<AlertCase[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [statsRes, casesRes] = await Promise.all([
        alertService.getStats(),
        alertService.getAlerts({ status: 'active', page: 1, pageSize: 50 }),
      ])
      setStats(statsRes.stats)
      setCases(casesRes.cases || [])
    } catch (e) {
      setError(e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="mb-8">
          <h1 className="text-lg font-semibold text-text-primary">我的告警</h1>
          <p className="text-xs text-text-muted mt-1">库存告警工单 · 只读视图 · 工单流转请前往管理端</p>
        </div>

        {loading && <p className="py-8 text-center text-sm text-text-muted">加载中…</p>}

        {!loading && error !== null && <ErrorCard error={error} onRetry={load} />}

        {!loading && !error && stats && (
          <>
            <div className="grid grid-cols-3 gap-4 mb-8">
              <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
                <div className="flex items-center gap-2 text-xs text-text-muted mb-1">
                  <AlertTriangle size={14} /> 严重告警
                </div>
                <div className="text-2xl font-semibold text-red-600">{stats.critical}</div>
              </div>
              <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
                <div className="flex items-center gap-2 text-xs text-text-muted mb-1">
                  <AlertTriangle size={14} /> 警告
                </div>
                <div className="text-2xl font-semibold text-amber-600">{stats.warning}</div>
              </div>
              <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
                <div className="flex items-center gap-2 text-xs text-text-muted mb-1">
                  <Package size={14} /> 已解决
                </div>
                <div className="text-2xl font-semibold text-green-600">{stats.resolved}</div>
              </div>
            </div>

            {cases.length === 0 ? (
              <div className="rounded-xl border border-dashed border-border-subtle bg-surface-base p-10 text-center">
                <p className="text-sm text-text-secondary">当前没有进行中的告警工单</p>
                <p className="mt-1 text-xs text-text-muted">库存在安全区间。历史工单与流转操作请前往管理端。</p>
                <Link href="/reports" className="mt-4 inline-block rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover">
                  前往报告中心查看库存分析
                </Link>
              </div>
            ) : (
              <ul className="space-y-2">
                {cases.map((c) => (
                  <li key={c.id} className="flex flex-wrap items-center gap-3 rounded-xl border border-border-subtle bg-surface-base p-4">
                    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${badge(LEVEL_BADGE[c.current_level])}`}>
                      {LEVEL_LABEL[c.current_level] ?? c.current_level}
                    </span>
                    <span className="font-mono text-xs text-text-primary">{c.product_id}</span>
                    <span className="text-xs text-text-secondary">{STATE_LABEL[c.current_state] ?? c.current_state}</span>
                    <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] text-gray-600">
                      {STATUS_LABEL[c.status] ?? c.status}
                    </span>
                    <span className="ml-auto text-[11px] text-text-muted">
                      发现于 {c.first_detected_at?.slice(0, 16).replace('T', ' ')}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </div>
  )
}
