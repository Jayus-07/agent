'use client'

import { useState } from 'react'
import { FileText, Loader2 } from 'lucide-react'
import { sendReport } from '@/lib/api'

const REPORT_TYPES = [
  { value: 'monthly_sales', label: '月度销售' },
  { value: 'project_progress', label: '项目进度' },
  { value: 'dept_summary', label: '部门汇总' },
]

export default function ReportPage() {
  const [reportType, setReportType] = useState('monthly_sales')
  const [report, setReport] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function handleGenerate() {
    setLoading(true)
    setError('')
    setReport('')
    try {
      const res = await sendReport(reportType)
      setReport(res.report || '无内容')
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="max-w-3xl mx-auto px-4 py-8">
      <h1 className="text-lg font-semibold mb-6 flex items-center gap-2">
        <FileText size={20} /> 报告生成
      </h1>

      <div className="flex items-center gap-4 mb-6">
        <select
          value={reportType}
          onChange={(e) => setReportType(e.target.value)}
          className="bg-[#2f2f2f] border border-[#3f3f3f] rounded-lg px-3 py-2.5 text-sm text-[#ececec] outline-none"
        >
          {REPORT_TYPES.map((t) => (
            <option key={t.value} value={t.value}>{t.label}</option>
          ))}
        </select>
        <button
          onClick={handleGenerate}
          disabled={loading}
          className="px-5 py-2.5 bg-[#ececec] text-[#171717] rounded-lg text-sm font-medium hover:bg-white disabled:opacity-30 transition-all"
        >
          {loading ? <Loader2 size={16} className="animate-spin" /> : '生成'}
        </button>
      </div>

      {error && (
        <div className="bg-red-900/20 border border-red-500/30 rounded-lg px-4 py-3 text-sm text-red-400 mb-4">
          {error}
        </div>
      )}

      {report && (
        <div className="bg-[#1a1a1a] border border-[#3f3f3f] rounded-xl p-5">
          <div className="markdown-body text-sm text-[#ececec]" dangerouslySetInnerHTML={{ __html: report }} />
        </div>
      )}
    </div>
  )
}
