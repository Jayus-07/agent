'use client'

import { useEffect, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { ArrowLeft, ExternalLink, ChevronDown, ChevronUp } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { reportService, type DailyReportDetail } from '@/services/reports'

export default function ReportDetailPage() {
  const { id } = useParams<{ id: string }>()
  const router = useRouter()
  const [report, setReport] = useState<DailyReportDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [showTech, setShowTech] = useState(false)

  useEffect(() => {
    reportService.getReport(id).then(r => {
      setReport(r.report)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [id])

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-sm text-text-muted">加载中...</p>
      </div>
    )
  }

  if (!report) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-sm text-text-muted">报告未找到</p>
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <button onClick={() => router.back()} className="p-1.5 rounded-lg hover:bg-black/5 text-text-muted">
            <ArrowLeft size={18} />
          </button>
          <div>
            <h1 className="text-lg font-semibold text-text-primary">
              经营日报 · {report.report_date}
            </h1>
            <p className="text-xs text-text-muted">
              生成时间: {report.created_at?.slice(0, 19)} · 状态: {report.status}
            </p>
          </div>
        </div>

        {/* Content: Business View */}
        <div className="bg-surface-base rounded-xl border border-border-subtle p-6 mb-4">
          <div className="prose prose-sm max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {report.report_content}
            </ReactMarkdown>
          </div>
        </div>

        {/* Tech View (collapsible) */}
        <div className="bg-surface-base rounded-xl border border-border-subtle">
          <button
            onClick={() => setShowTech(!showTech)}
            className="w-full flex items-center justify-between px-6 py-3 text-sm text-text-secondary hover:bg-black/5 rounded-xl transition-colors"
          >
            <span className="flex items-center gap-2">
              📐 技术视图
            </span>
            {showTech ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </button>
          {showTech && (
            <div className="px-6 pb-4 space-y-3">
              <div className="text-xs text-text-muted space-y-1">
                <p>Report ID: <code className="text-[11px] bg-black/5 px-1 rounded">{report.id}</code></p>
                <p>KPI Summary: <code className="text-[11px] bg-black/5 px-1 rounded">{JSON.stringify(report.kpi_summary)}</code></p>
              </div>
              {report.trace_id && (
                <a
                  href={`/observability/traces/${report.trace_id}`}
                  target="_blank"
                  className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
                >
                  查看完整 Trace <ExternalLink size={12} />
                </a>
              )}
              {!report.trace_id && (
                <p className="text-xs text-text-muted">无关联 Trace</p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
