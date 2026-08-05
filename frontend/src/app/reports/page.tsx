'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { FileText, AlertTriangle, TrendingUp, Package, ChevronRight } from 'lucide-react'
import { reportService, type DailyReportSummary } from '@/services/reports'
import { clsx } from 'clsx'

interface WorkflowMeta {
  name: string; description: string; category: string
}

const STATUS_MAP: Record<string, { label: string; cls: string }> = {
  success: { label: '成功', cls: 'bg-green-100 text-green-700' },
  failed: { label: '失败', cls: 'bg-red-100 text-red-700' },
  partial: { label: '部分成功', cls: 'bg-yellow-100 text-yellow-700' },
}

export default function ReportsPage() {
  const router = useRouter()
  const [reports, setReports] = useState<DailyReportSummary[]>([])
  const [latestKpi, setLatestKpi] = useState<DailyReportSummary['kpi_summary'] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    async function load() {
      try {
        const [listRes, latestRes] = await Promise.all([
          reportService.getReports({ type: 'daily_report' }),
          reportService.getLatestReport('daily_report'),
        ])
        setReports(listRes.reports || [])
        setLatestKpi(latestRes.report?.kpi_summary ?? null)
        setError(null)
      } catch (e) {
        setError(e instanceof Error ? e.message : '加载报告失败')
        setReports([])
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  const weekDay = (dateStr: string) => {
    const d = new Date(dateStr)
    return ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.getDay()]
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-lg font-semibold text-text-primary">报告中心</h1>
          <p className="text-xs text-text-muted mt-1">Workflow 自动生成 · 按日期归档</p>
        </div>

        {/* KPI Summary Cards */}
        {latestKpi && (
          <div className="grid grid-cols-4 gap-4 mb-8">
            <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
              <div className="flex items-center gap-2 text-xs text-text-muted mb-1">
                <Package size={14} /> 商品总数
              </div>
              <div className="text-2xl font-semibold text-text-primary">{latestKpi.total_products ?? '-'}</div>
            </div>
            <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
              <div className="flex items-center gap-2 text-xs text-text-muted mb-1">
                <AlertTriangle size={14} /> 异常库存
              </div>
              <div className={clsx('text-2xl font-semibold', (latestKpi.alert_count ?? 0) > 0 ? 'text-red-500' : 'text-text-primary')}>
                {latestKpi.alert_count ?? '-'}
              </div>
            </div>
            <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
              <div className="flex items-center gap-2 text-xs text-text-muted mb-1">
                <TrendingUp size={14} /> 销售记录
              </div>
              <div className="text-2xl font-semibold text-text-primary">{latestKpi.sales_records ?? '-'}</div>
            </div>
            <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
              <div className="flex items-center gap-2 text-xs text-text-muted mb-1">
                <FileText size={14} /> 日报日期
              </div>
              <div className="text-lg font-semibold text-text-primary">{latestKpi.report_date ?? '-'}</div>
            </div>
          </div>
        )}

        {/* Report List */}
        <div className="space-y-2">
          {loading && <p className="text-xs text-text-muted py-4">加载中...</p>}
          {!loading && error && (
            <p className="text-xs text-red-500 py-4">加载失败：{error}</p>
          )}
          {!loading && !error && reports.length === 0 && (
            <p className="text-xs text-text-muted py-4">暂无报告，请先运行日报 Workflow</p>
          )}
          {reports.map(r => {
            const status = STATUS_MAP[r.status] || STATUS_MAP.success
            return (
              <button
                key={r.id}
                onClick={() => router.push(`/reports/${r.id}`)}
                className="w-full bg-surface-base rounded-xl border border-border-subtle p-4 hover:shadow-card transition-shadow text-left"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <FileText size={18} className="text-accent" />
                    <div>
                      <span className="text-sm text-text-primary">
                        {r.report_date} · {weekDay(r.report_date)}
                      </span>
                      <span className={clsx('ml-2 text-[10px] px-2 py-0.5 rounded-full', status.cls)}>
                        {status.label}
                      </span>
                    </div>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-text-muted">
                    {r.kpi_summary?.alert_count ? (
                      <span className="text-red-500">⚠ {r.kpi_summary.alert_count} 异常</span>
                    ) : (
                      <span className="text-green-600">✅ 正常</span>
                    )}
                    <ChevronRight size={14} />
                  </div>
                </div>
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}
