'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  ChevronDown,
  ChevronRight,
  ChevronUp,
  ExternalLink,
  FileText,
  RefreshCw,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  reportService,
  type DailyReportDetail,
  type DailyReportSummary,
} from '@/api/reports'
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

const STATUS_MAP: Record<string, { label: string; cls: string }> = {
  success: { label: '已完成', cls: 'bg-emerald-50 text-emerald-700 border border-emerald-200' },
  failed: { label: '生成失败', cls: 'bg-rose-50 text-rose-700 border border-rose-200' },
  partial: { label: '部分生成', cls: 'bg-amber-50 text-amber-700 border border-amber-200' },
}

export default function ReportsPage() {
  const router = useRouter()
  const [reports, setReports] = useState<DailyReportSummary[]>([])
  const [latestKpi, setLatestKpi] = useState<DailyReportSummary['kpi_summary'] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // 详情抽屉状态
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [reportDetail, setReportDetail] = useState<DailyReportDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [showTech, setShowTech] = useState(false)

  async function loadData() {
    setLoading(true)
    setError(null)
    try {
      const [listRes, latestRes] = await Promise.all([
        reportService.getReports({ type: 'daily_report' }),
        reportService.getLatestReport('daily_report'),
      ])
      setReports(listRes.reports || [])
      setLatestKpi(latestRes.report?.kpi_summary ?? null)
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载报告中心失败')
      setReports([])
    } finally {
      setLoading(false)
    }
  }

  async function openDrawer(id: string) {
    setSelectedId(id)
    setDetailLoading(true)
    setShowTech(false)
    try {
      const res = await reportService.getReport(id)
      setReportDetail(res.report)
    } catch {
      setReportDetail(null)
    } finally {
      setDetailLoading(false)
    }
  }

  useEffect(() => {
    void loadData()
  }, [])

  const weekDay = (dateStr: string) => {
    const d = new Date(dateStr)
    return ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.getDay()] || ''
  }

  const metricItems: StatMetricItem[] = [
    {
      label: '监控商品总数',
      value: latestKpi?.total_products ?? '-',
      hint: '当前纳入巡检商品',
    },
    {
      label: '库存异常预警',
      value: latestKpi?.alert_count ?? 0,
      tone: (latestKpi?.alert_count ?? 0) > 0 ? 'danger' : 'default',
      hint: '需补货或调配',
    },
    {
      label: '关联销售记录',
      value: latestKpi?.sales_records ?? '-',
      hint: '当日流水总计',
    },
    {
      label: '最新日报归档',
      value: latestKpi?.report_date ?? '-',
      tone: 'accent',
      hint: '生成批次时间',
    },
  ]

  return (
    <AssetPageShell
      title="报告中心"
      desc="Workflow 自动生成 · 经营日报与业务归档"
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

        {/* 标题栏与数量 */}
        <div className="flex items-center justify-between border-b border-border-subtle pb-3">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-text-primary">经营日报归档列表</span>
            <span className="rounded-full bg-black/5 px-2 py-0.5 text-[11px] text-text-muted">
              共 {reports.length} 篇
            </span>
          </div>
        </div>

        {/* 列表状态 */}
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
          <ErrorState title="加载报告列表失败" message={error} onRetry={loadData} />
        )}

        {!loading && !error && reports.length === 0 && (
          <EmptyState
            title="经营日报"
            description="暂未归档任何日报记录，请先在任务或调度中运行日报生成工作流。"
            onAction={loadData}
            actionLabel="刷新列表"
          />
        )}

        {!loading && !error && reports.length > 0 && (
          <div className="space-y-2.5">
            {reports.map((r) => {
              const status = STATUS_MAP[r.status] || STATUS_MAP.success
              const isAlert = Boolean(r.kpi_summary?.alert_count && r.kpi_summary.alert_count > 0)
              return (
                <button
                  key={r.id}
                  type="button"
                  onClick={() => openDrawer(r.id)}
                  className={clsx(
                    ASSET_SURFACE,
                    'w-full p-4 text-left transition-all hover:border-accent/40 hover:shadow-md',
                  )}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3.5">
                      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-accent/20 bg-accent-soft text-accent">
                        <FileText size={18} />
                      </span>
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="text-[14px] font-semibold text-text-primary">
                            经营日报 · {r.report_date}
                          </span>
                          <span className="text-xs text-text-muted">({weekDay(r.report_date)})</span>
                          <span className={clsx('rounded-full px-2 py-0.5 text-[11px] font-medium', status.cls)}>
                            {status.label}
                          </span>
                        </div>
                        <div className="mt-1 text-xs text-text-muted">
                          归档时间: {r.created_at?.slice(0, 16) || '-'}
                          {r.kpi_summary?.total_products !== undefined && (
                            <span> · 巡检商品 {r.kpi_summary.total_products} 款</span>
                          )}
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-4 text-xs text-text-muted">
                      {isAlert ? (
                        <span className="rounded-md bg-rose-50 px-2 py-1 text-rose-600">
                          ⚠ {r.kpi_summary?.alert_count} 项异常
                        </span>
                      ) : (
                        <span className="rounded-md bg-emerald-50 px-2 py-1 text-emerald-600">
                          ✅ 水位正常
                        </span>
                      )}
                      <div className="flex items-center gap-1">
                        <span className="text-[12px]">查阅报告</span>
                        <ChevronRight size={15} />
                      </div>
                    </div>
                  </div>
                </button>
              )
            })}
          </div>
        )}
      </div>

      {/* 统一右侧报告预览抽屉 */}
      <DetailDrawer
        open={Boolean(selectedId)}
        onClose={() => {
          setSelectedId(null)
          setReportDetail(null)
        }}
        title={reportDetail ? `经营日报 · ${reportDetail.report_date}` : '报告预览'}
        subtitle={
          reportDetail
            ? `生成时间: ${reportDetail.created_at?.slice(0, 19)} · 状态: ${STATUS_MAP[reportDetail.status]?.label || reportDetail.status}`
            : undefined
        }
        actions={
          reportDetail && (
            <button
              type="button"
              onClick={() => router.push(`/reports/${reportDetail.id}`)}
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-text-muted transition-colors hover:bg-black/5 hover:text-text-primary"
              title="打开独立全屏页"
            >
              全屏查阅 <ExternalLink size={12} />
            </button>
          )
        }
      >
        {detailLoading && (
          <div className="space-y-4 py-8">
            <div className="h-6 w-1/3 animate-pulse rounded bg-black/5" />
            <div className="h-40 animate-pulse rounded bg-black/5" />
            <div className="h-60 animate-pulse rounded bg-black/5" />
          </div>
        )}

        {!detailLoading && reportDetail && (
          <div className="space-y-5">
            {/* 报告正文 */}
            <div className={clsx(ASSET_SURFACE, 'p-6')}>
              <div className="prose prose-sm max-w-none text-text-primary">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {reportDetail.report_content}
                </ReactMarkdown>
              </div>
            </div>

            {/* 技术视图折叠 */}
            <div className={clsx(ASSET_SURFACE, 'overflow-hidden')}>
              <button
                type="button"
                onClick={() => setShowTech(!showTech)}
                className="flex w-full items-center justify-between px-5 py-3 text-xs font-semibold text-text-secondary transition-colors hover:bg-black/5"
              >
                <span>📐 技术元数据与 Trace</span>
                {showTech ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
              </button>
              {showTech && (
                <div className="border-t border-border-subtle bg-bg-elevated/40 p-4 text-xs text-text-muted space-y-2.5">
                  <div>
                    Report ID: <code className="rounded bg-black/5 px-1 py-0.5 font-mono">{reportDetail.id}</code>
                  </div>
                  {reportDetail.kpi_summary && (
                    <div>
                      KPI 摘要:
                      <pre className="mt-1 overflow-x-auto rounded bg-black/5 p-2 font-mono text-[11px] text-text-secondary">
                        {JSON.stringify(reportDetail.kpi_summary, null, 2)}
                      </pre>
                    </div>
                  )}
                  <div className="pt-1">
                    {reportDetail.trace_id ? (
                      <a
                        href={`/observability/monitoring`}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 text-accent hover:underline"
                      >
                        在运行监控中定位 Trace ({reportDetail.trace_id.slice(0, 12)}...) <ExternalLink size={12} />
                      </a>
                    ) : (
                      <span>无关联执行 Trace</span>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </DetailDrawer>
    </AssetPageShell>
  )
}
