'use client'

import { useEffect, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import {
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  ExternalLink,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { reportService, type DailyReportDetail } from '@/api/reports'
import {
  AssetActionButton,
  AssetPageShell,
  ASSET_SURFACE,
} from '@/components/layout/AssetPageShell'
import ErrorState from '@/components/shared/ErrorState'
import { clsx } from 'clsx'

export default function ReportDetailPage() {
  const { id } = useParams<{ id: string }>()
  const router = useRouter()
  const [report, setReport] = useState<DailyReportDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showTech, setShowTech] = useState(false)

  async function loadReport() {
    setLoading(true)
    setError(null)
    try {
      const res = await reportService.getReport(id)
      setReport(res.report)
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载报告详情失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadReport()
  }, [id])

  if (loading) {
    return (
      <AssetPageShell title="经营日报查阅">
        <div className="space-y-4 py-8">
          <div className="h-10 w-1/3 animate-pulse rounded-lg bg-black/5" />
          <div className="h-64 animate-pulse rounded-lg bg-black/5" />
        </div>
      </AssetPageShell>
    )
  }

  if (error || !report) {
    return (
      <AssetPageShell
        title="经营日报查阅"
        actions={
          <AssetActionButton icon={<ArrowLeft size={14} />} onClick={() => router.push('/reports')}>
            返回列表
          </AssetActionButton>
        }
      >
        <ErrorState title="报告加载失败" message={error || '未找到该日报记录'} onRetry={loadReport} />
      </AssetPageShell>
    )
  }

  return (
    <AssetPageShell
      title={`经营日报 · ${report.report_date}`}
      desc={`生成时间: ${report.created_at?.slice(0, 19) || '-'} · 状态: ${report.status}`}
      actions={
        <AssetActionButton icon={<ArrowLeft size={14} />} onClick={() => router.push('/reports')}>
          返回列表
        </AssetActionButton>
      }
    >
      <div className="mx-auto max-w-4xl space-y-6">
        {/* 正文卡片 */}
        <div className={clsx(ASSET_SURFACE, 'p-8')}>
          <div className="prose prose-sm max-w-none text-text-primary">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {report.report_content}
            </ReactMarkdown>
          </div>
        </div>

        {/* 技术视图折叠卡 */}
        <div className={clsx(ASSET_SURFACE, 'overflow-hidden')}>
          <button
            type="button"
            onClick={() => setShowTech(!showTech)}
            className="flex w-full items-center justify-between px-6 py-3.5 text-xs font-semibold text-text-secondary transition-colors hover:bg-black/5"
          >
            <span>📐 技术元数据与 Trace</span>
            {showTech ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </button>
          {showTech && (
            <div className="space-y-3 border-t border-border-subtle bg-bg-elevated/40 p-5 text-xs text-text-muted">
              <div>
                Report ID: <code className="rounded bg-black/5 px-1.5 py-0.5 font-mono">{report.id}</code>
              </div>
              {report.kpi_summary && (
                <div>
                  KPI 摘要元数据:
                  <pre className="mt-1.5 overflow-x-auto rounded bg-black/5 p-3 font-mono text-[11px] text-text-secondary">
                    {JSON.stringify(report.kpi_summary, null, 2)}
                  </pre>
                </div>
              )}
              <div className="pt-2">
                {report.trace_id ? (
                  <a
                    href={`/observability/monitoring`}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1.5 text-accent hover:underline"
                  >
                    在运行控制中心查看关联 Trace ({report.trace_id.slice(0, 12)}...) <ExternalLink size={13} />
                  </a>
                ) : (
                  <span>无关联执行 Trace</span>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </AssetPageShell>
  )
}
