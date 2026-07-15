'use client'

import { CheckCircle2, AlertCircle } from 'lucide-react'
import { useState, useEffect } from 'react'
import { PIPELINE_JOBS } from '@/services/mock/pipeline'
import { dataService } from '@/lib/services/dataService'

export default function HistoryPage() {
  const [jobs, setJobs] = useState(PIPELINE_JOBS)
  useEffect(() => { dataService.getPipelineHistory().then(j => { if (j.length) setJobs(j) }) }, [])
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="mb-8">
          <h1 className="text-lg font-semibold text-text-primary">执行历史</h1>
          <p className="text-xs text-text-muted mt-1">全部清洗任务的执行记录和结果</p>
        </div>
        <div className="bg-surface-base rounded-xl border border-border-subtle overflow-hidden">
          <table className="w-full text-xs">
            <thead><tr className="border-b border-border-subtle bg-surface-elevated text-text-muted">
              {['任务ID', '任务名称', '输入', '输出', '异常', '质量', '耗时', '状态'].map(h => <th key={h} className="text-left px-4 py-2.5 font-medium">{h}</th>)}
            </tr></thead>
            <tbody>
              {[...jobs, ...jobs.map(j => ({ ...j, id: j.id + 'b', status: 'done' as const }))].slice(0, 8).map(j => (
                <tr key={j.id} className="border-b border-border-subtle hover:bg-surface-hover transition-colors">
                  <td className="px-4 py-2.5 font-mono text-text-muted">{j.id}</td>
                  <td className="px-4 py-2.5 text-text-primary">{j.name}</td>
                  <td className="px-4 py-2.5">{j.inputRows.toLocaleString()}</td>
                  <td className="px-4 py-2.5">{j.outputRows.toLocaleString()}</td>
                  <td className="px-4 py-2.5 text-red-500">{j.errors}</td>
                  <td className="px-4 py-2.5">
                    <span className={j.quality >= 95 ? 'text-green-500' : j.quality >= 90 ? 'text-amber-500' : 'text-red-500'}>{j.quality}%</span>
                  </td>
                  <td className="px-4 py-2.5 text-text-muted">{j.elapsed}</td>
                  <td className="px-4 py-2.5">
                    {j.status === 'done' ? <CheckCircle2 size={13} className="text-green-500" /> : <AlertCircle size={13} className="text-red-500" />}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
