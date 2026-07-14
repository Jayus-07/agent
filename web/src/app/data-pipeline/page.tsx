'use client'

import { useState, useEffect } from 'react'
import { CheckCircle2, Clock, AlertCircle, ArrowRight, Database, RefreshCw } from 'lucide-react'
import { PIPELINE_JOBS } from '@/services/mock/pipeline'
import { dataService } from '@/lib/services/dataService'

const DCC_STAGES = [
  { name: '数据获取', key: 'fetch' },
  { name: '数据解析', key: 'parse' },
  { name: '数据清洗', key: 'clean' },
  { name: '数据分析', key: 'analyze' },
]

export default function PipelinePage() {
  const [jobs, setJobs] = useState(PIPELINE_JOBS)
  const [dccJobs, setDccJobs] = useState<any[]>([])
  const [loading, setLoading] = useState(false)

  const loadHistory = async () => {
    setLoading(true)
    try {
      // 加载两种历史：上传文件清洗 + DCC 采集
      const [pipelineRes, dccRes] = await Promise.allSettled([
        dataService.getPipelineHistory(),
        dataService.collectHistory(10),
      ])

      if (pipelineRes.status === 'fulfilled' && pipelineRes.value.length)
        setJobs(pipelineRes.value as any)

      if (dccRes.status === 'fulfilled' && dccRes.value?.jobs?.length)
        setDccJobs(dccRes.value.jobs)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadHistory() }, [])

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">数据处理中心</h1>
            <p className="text-xs text-text-muted mt-1">
              上传文件清洗 + DCC Pipeline：数据获取 → 解析 → 清洗 → 分析 → 入库
            </p>
          </div>
          <button onClick={loadHistory} disabled={loading}
            className="p-2 rounded-lg hover:bg-surface-elevated transition-colors text-text-muted">
            <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>

        <div className="space-y-4">
          {/* DCC 采集任务 */}
          {dccJobs.length > 0 && (
            <>
              <div className="flex items-center gap-2 mb-2">
                <Database size={14} className="text-accent" />
                <span className="text-xs font-medium text-text-secondary">Data Collection Center</span>
              </div>
              {dccJobs.map(job => (
                <div key={job.task_id} className="bg-surface-base rounded-xl border border-accent/20 p-5">
                  <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center gap-3">
                      {job.status === 'success' ? <CheckCircle2 size={18} className="text-green-500" />
                       : job.status === 'failed' ? <AlertCircle size={18} className="text-red-500" />
                       : <Clock size={18} className="text-amber-500" />}
                      <div>
                        <span className="text-sm font-medium text-text-primary">
                          {job.source?.split('/').pop()?.replace('.json', '') || job.task_id}
                        </span>
                        <span className="text-[10px] text-text-muted ml-2">{(job.elapsed_ms || 0).toFixed(0)}ms</span>
                      </div>
                    </div>
                    <DccStatusBadge status={job.status} />
                  </div>

                  {/* DCC 5 阶段 */}
                  <div className="flex items-center gap-1.5 flex-wrap mb-4">
                    {DCC_STAGES.map((s, i) => (
                      <div key={s.key} className="flex items-center gap-1.5">
                        <div className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs ${
                          job.status === 'success' ? 'bg-green-50 text-green-600'
                          : job.status === 'failed' ? 'bg-surface-elevated text-text-muted'
                          : 'bg-surface-elevated text-text-muted'
                        }`}>
                          <CheckCircle2 size={11} />
                          {s.name}
                        </div>
                        {i < DCC_STAGES.length - 1 && <ArrowRight size={11} className="text-text-muted" />}
                      </div>
                    ))}
                  </div>

                  <div className="grid grid-cols-3 gap-3 text-center">
                    <div><div className="text-xs text-text-muted">解析</div><div className="text-sm font-semibold text-text-primary">{job.parsed_rows || 0}</div></div>
                    <div><div className="text-xs text-text-muted">清洗</div><div className="text-sm font-semibold text-text-primary">{job.cleaned_rows || 0}</div></div>
                    <div><div className="text-xs text-text-muted">耗时</div><div className="text-sm font-semibold text-text-primary">{(job.elapsed_ms || 0).toFixed(0)}ms</div></div>
                  </div>
                </div>
              ))}
            </>
          )}

          {/* 文件清洗任务 (mock + API) */}
          {jobs.map(job => (
            <div key={job.id} className="bg-surface-base rounded-xl border border-border-subtle p-5">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-3">
                  {job.status === 'done' ? <CheckCircle2 size={18} className="text-green-500" />
                   : job.status === 'running' ? <Clock size={18} className="text-amber-500 animate-pulse" />
                   : <AlertCircle size={18} className="text-red-500" />}
                  <div>
                    <span className="text-sm font-medium text-text-primary">{job.name}</span>
                    <span className="text-[10px] text-text-muted ml-2">{job.elapsed}</span>
                  </div>
                </div>
                <StatusBadge status={job.status} />
              </div>

              {/* Pipeline stages */}
              <div className="flex items-center gap-1.5 flex-wrap mb-4">
                {job.stages.map((s: any, i: number) => (
                  <div key={s.name} className="flex items-center gap-1.5">
                    <div className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs ${
                      s.status === 'done' ? 'bg-green-50 text-green-600' : s.status === 'running' ? 'bg-amber-50 text-amber-600' : 'bg-surface-elevated text-text-muted'}`}>
                      {s.status === 'done' ? <CheckCircle2 size={11} /> : s.status === 'running' ? <Clock size={11} /> : <div className="w-[11px] h-[11px] rounded-full border border-border-default" />}
                      {s.name}
                    </div>
                    {i < job.stages.length - 1 && <ArrowRight size={11} className="text-text-muted" />}
                  </div>
                ))}
              </div>

              {/* Stats */}
              <div className="grid grid-cols-4 gap-3 text-center">
                <div><div className="text-xs text-text-muted">输入</div><div className="text-sm font-semibold text-text-primary">{job.inputRows.toLocaleString()}</div></div>
                <div><div className="text-xs text-text-muted">输出</div><div className="text-sm font-semibold text-text-primary">{job.outputRows.toLocaleString()}</div></div>
                <div><div className="text-xs text-text-muted">异常</div><div className="text-sm font-semibold text-red-500">{job.errors.toLocaleString()}</div></div>
                <div><div className="text-xs text-text-muted">质量</div><div className="text-sm font-semibold text-green-500">{job.quality}%</div></div>
              </div>
            </div>
          ))}

          {!dccJobs.length && !jobs.length && (
            <div className="text-center py-12 text-text-muted text-sm">
              暂无任务记录。去 <a href="/data-source" className="text-accent underline">数据接入中心</a> 触发一次采集吧。
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const c: Record<string, string> = { done: 'bg-green-50 text-green-600', running: 'bg-amber-50 text-amber-600', error: 'bg-red-50 text-red-600' }
  const l: Record<string, string> = { done: '已完成', running: '运行中', error: '失败' }
  return <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${c[status]}`}>{l[status]}</span>
}

function DccStatusBadge({ status }: { status: string }) {
  const c: Record<string, string> = { success: 'bg-green-50 text-green-600', partial: 'bg-amber-50 text-amber-600', failed: 'bg-red-50 text-red-600' }
  const l: Record<string, string> = { success: '成功', partial: '部分成功', failed: '失败' }
  return <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${c[status] || c.failed}`}>{l[status] || status}</span>
}
