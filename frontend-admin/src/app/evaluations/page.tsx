'use client'

import { useEffect, useState, useCallback } from 'react'
import { RefreshCw, ChevronDown, ChevronRight, CheckCircle2, XCircle, AlertCircle, SkipForward } from 'lucide-react'
import { evaluationService, type RunSummary, type EvalRunDetail } from '@/api/evaluation'
import { useToast } from '@/components/shared/Toast'
import EmptyState from '@/components/shared/EmptyState'
import dynamic from 'next/dynamic'

// recharts 较重，拆为独立 chunk 懒加载，避免路由切换时阻塞渲染
const TrendCharts = dynamic(() => import('./TrendCharts'), {
  ssr: false,
  loading: () => <div className="h-48 rounded-xl bg-black/5 animate-pulse" />,
})

const STATUS_ICON = {
  pass: <CheckCircle2 size={14} className="text-green-500" />,
  fail: <XCircle size={14} className="text-red-500" />,
  error: <AlertCircle size={14} className="text-orange-500" />,
  skip: <SkipForward size={14} className="text-gray-400" />,
}

export default function EvaluationsPage() {
  const toast = useToast()
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedRun, setSelectedRun] = useState<string | null>(null)
  const [runDetail, setRunDetail] = useState<EvalRunDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [expandedCase, setExpandedCase] = useState<string | null>(null)

  const loadRuns = useCallback(async () => {
    setLoading(true)
    try {
      const data = await evaluationService.listRuns(50)
      setRuns(data)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '加载评测记录失败')
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => { loadRuns() }, [loadRuns])

  const handleSelectRun = async (runId: string) => {
    if (selectedRun === runId) {
      setSelectedRun(null)
      setRunDetail(null)
      return
    }
    setSelectedRun(runId)
    setDetailLoading(true)
    try {
      const detail = await evaluationService.getRun(runId)
      setRunDetail(detail)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '加载评测详情失败')
    } finally {
      setDetailLoading(false)
    }
  }

  const latestRun = runs[0]
  const trendData = [...runs].reverse().map(r => ({
    name: r.timestamp ? new Date(r.timestamp).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' }) : r.run_id.slice(5, 10),
    top1: +(r.top1_accuracy * 100).toFixed(1),
    pass: +(r.pass_rate * 100).toFixed(1),
    faithfulness: +(r.faithfulness * 100).toFixed(1),
    correctness: +(r.answer_correctness * 100).toFixed(1),
    recall5: +(r.recall_at_5 * 100).toFixed(1),
    reject: +(r.reject_accuracy * 100).toFixed(1),
    mrr: +(r.mrr * 100).toFixed(1),
    ndcg: +(r.ndcg_at_10 * 100).toFixed(1),
  }))

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-6xl mx-auto px-6 py-8">
        {/* 头部 */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">评测结果</h1>
            <p className="text-xs text-text-muted mt-0.5">RAG 检索评测历史与详情</p>
          </div>
          <button
            onClick={loadRuns}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-border-subtle text-text-secondary hover:text-text-primary transition-colors disabled:opacity-50"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>

        {/* 指标卡片 */}
        {latestRun && (
          <div className="space-y-3 mb-6">
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              <MetricCard label="Top-1 准确率" value={latestRun.top1_accuracy} threshold={0.85} />
              <MetricCard label="通过率" value={latestRun.pass_rate} threshold={0.85} />
              <MetricCard label="Recall@5" value={latestRun.recall_at_5} threshold={0.85} />
              <MetricCard label="MRR" value={latestRun.mrr} threshold={0.85} />
              <MetricCard label="NDCG@10" value={latestRun.ndcg_at_10} threshold={0.85} />
            </div>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              <MetricCard label="忠实度" value={latestRun.faithfulness} threshold={0.85} />
              <MetricCard label="答案正确性" value={latestRun.answer_correctness} threshold={0.85} />
              <MetricCard label="拒答准确率" value={latestRun.reject_accuracy} threshold={0.85} />
              <MetricCard label="运行总数" value={runs.length} isCount />
              <MetricCard label="最近运行" value={latestRun.timestamp ? new Date(latestRun.timestamp).toLocaleDateString('zh-CN') : '-'} isText />
            </div>
          </div>
        )}

        {/* 趋势图（recharts 懒加载） */}
        {trendData.length > 1 && <TrendCharts data={trendData} />}

        {/* 运行历史列表 */}
        <div className="bg-surface-base rounded-xl border border-border-subtle">
          <div className="px-4 py-3 border-b border-border-subtle">
            <h3 className="text-xs font-medium text-text-secondary">运行记录</h3>
          </div>
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <RefreshCw size={20} className="animate-spin text-text-muted" />
            </div>
          ) : runs.length === 0 ? (
            <div className="p-6">
              <EmptyState
                title="评测记录"
                description="触发一次评测后，运行记录会出现在这里"
                actionHref="/"
                actionLabel="返回管理端首页"
              />
            </div>
          ) : (
            <div className="divide-y divide-border-subtle">
              {runs.map(run => (
                <div key={run.run_id}>
                  <button
                    onClick={() => handleSelectRun(run.run_id)}
                    className="w-full flex items-center gap-4 px-4 py-3 hover:bg-black/[0.02] transition-colors text-left"
                  >
                    {selectedRun === run.run_id ? (
                      <ChevronDown size={14} className="text-text-muted shrink-0" />
                    ) : (
                      <ChevronRight size={14} className="text-text-muted shrink-0" />
                    )}
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-medium text-text-primary font-mono">{run.run_id}</p>
                      <p className="text-[10px] text-text-muted mt-0.5">
                        {run.timestamp ? new Date(run.timestamp).toLocaleString('zh-CN') : '-'}
                      </p>
                    </div>
                    <div className="flex items-center gap-3 text-xs">
                      <div className="text-right">
                        <p className="text-[10px] text-text-muted">Top-1</p>
                        <p className={`font-medium ${run.top1_accuracy >= 0.85 ? 'text-green-600' : 'text-red-500'}`}>
                          {(run.top1_accuracy * 100).toFixed(1)}%
                        </p>
                      </div>
                      <div className="text-right">
                        <p className="text-[10px] text-text-muted">通过率</p>
                        <p className={`font-medium ${run.pass_rate >= 0.85 ? 'text-green-600' : 'text-red-500'}`}>
                          {(run.pass_rate * 100).toFixed(1)}%
                        </p>
                      </div>
                      <div className="text-right">
                        <p className="text-[10px] text-text-muted">Recall@5</p>
                        <p className={`font-medium ${run.recall_at_5 >= 0.85 ? 'text-green-600' : run.recall_at_5 > 0 ? 'text-red-500' : 'text-text-muted'}`}>
                          {run.recall_at_5 > 0 ? `${(run.recall_at_5 * 100).toFixed(1)}%` : '—'}
                        </p>
                      </div>
                      <div className="text-right">
                        <p className="text-[10px] text-text-muted">MRR</p>
                        <p className={`font-medium ${run.mrr >= 0.85 ? 'text-green-600' : run.mrr > 0 ? 'text-red-500' : 'text-text-muted'}`}>
                          {run.mrr > 0 ? `${(run.mrr * 100).toFixed(1)}%` : '—'}
                        </p>
                      </div>
                      <div className="text-right">
                        <p className="text-[10px] text-text-muted">NDCG</p>
                        <p className={`font-medium ${run.ndcg_at_10 >= 0.85 ? 'text-green-600' : run.ndcg_at_10 > 0 ? 'text-red-500' : 'text-text-muted'}`}>
                          {run.ndcg_at_10 > 0 ? `${(run.ndcg_at_10 * 100).toFixed(1)}%` : '—'}
                        </p>
                      </div>
                      <div className="text-right">
                        <p className="text-[10px] text-text-muted">忠实度</p>
                        <p className={`font-medium ${run.faithfulness >= 0.85 ? 'text-green-600' : run.faithfulness > 0 ? 'text-red-500' : 'text-text-muted'}`}>
                          {run.faithfulness > 0 ? `${(run.faithfulness * 100).toFixed(1)}%` : '—'}
                        </p>
                      </div>
                      <div className="text-right">
                        <p className="text-[10px] text-text-muted">正确性</p>
                        <p className={`font-medium ${run.answer_correctness >= 0.85 ? 'text-green-600' : run.answer_correctness > 0 ? 'text-red-500' : 'text-text-muted'}`}>
                          {run.answer_correctness > 0 ? `${(run.answer_correctness * 100).toFixed(1)}%` : '—'}
                        </p>
                      </div>
                      <div className="text-right">
                        <p className="text-[10px] text-text-muted">拒答</p>
                        <p className={`font-medium ${run.reject_accuracy >= 0.85 ? 'text-green-600' : run.reject_accuracy > 0 ? 'text-red-500' : 'text-text-muted'}`}>
                          {run.reject_accuracy > 0 ? `${(run.reject_accuracy * 100).toFixed(1)}%` : '—'}
                        </p>
                      </div>
                    </div>
                  </button>

                  {/* 展开的详情 */}
                  {selectedRun === run.run_id && (
                    <div className="px-4 pb-4 bg-black/[0.01]">
                      {detailLoading ? (
                        <div className="flex items-center justify-center py-8">
                          <RefreshCw size={16} className="animate-spin text-text-muted" />
                          <span className="text-xs text-text-muted ml-2">加载中...</span>
                        </div>
                      ) : runDetail ? (
                        <CaseDetailList
                          detail={runDetail}
                          expandedCase={expandedCase}
                          onToggleCase={setExpandedCase}
                        />
                      ) : (
                        <p className="text-xs text-text-muted text-center py-4">无法加载详情</p>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function MetricCard({ label, value, threshold, isCount, isText }: {
  label: string
  value: number | string
  threshold?: number
  isCount?: boolean
  isText?: boolean
}) {
  const getColor = () => {
    if (isText || isCount) return 'text-text-primary'
    if (typeof value === 'number' && threshold && value >= threshold) return 'text-green-600'
    if (typeof value === 'number' && threshold) return 'text-red-500'
    return 'text-text-primary'
  }

  const displayValue = isText || isCount
    ? value
    : typeof value === 'number'
      ? `${(value * 100).toFixed(1)}%`
      : value

  return (
    <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
      <p className="text-[10px] text-text-muted">{label}</p>
      <p className={`text-xl font-bold mt-1 ${getColor()}`}>{displayValue}</p>
    </div>
  )
}

function CaseDetailList({ detail, expandedCase, onToggleCase }: {
  detail: EvalRunDetail
  expandedCase: string | null
  onToggleCase: (id: string | null) => void
}) {
  const { results, tier_summaries } = detail.report

  return (
    <div className="space-y-3">
      {/* Tier 汇总 */}
      {tier_summaries.length > 0 && (
        <div className="flex gap-3 mb-3">
          {tier_summaries.map(ts => (
            <div
              key={ts.tier}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] ${
                ts.passed_threshold ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'
              }`}
            >
              {ts.passed_threshold ? <CheckCircle2 size={10} /> : <XCircle size={10} />}
              {ts.tier}: {(ts.pass_rate * 100).toFixed(0)}% (阈值 {(ts.threshold * 100).toFixed(0)}%)
            </div>
          ))}
        </div>
      )}

      {/* Case 列表 */}
      <div className="space-y-1">
        {results.map(r => (
          <div key={r.case_id} className="border border-border-subtle rounded-lg overflow-hidden">
            <button
              onClick={() => onToggleCase(expandedCase === r.case_id ? null : r.case_id)}
              className="w-full flex items-center gap-2 px-3 py-2 hover:bg-black/[0.02] transition-colors text-left"
            >
              {STATUS_ICON[r.status]}
              <span className="text-xs font-mono text-text-primary flex-1">{r.case_id}</span>
              <span className="text-[10px] text-text-muted">{r.duration_ms}ms</span>
            </button>
            {expandedCase === r.case_id && (
              <div className="px-3 py-2 bg-black/[0.01] border-t border-border-subtle space-y-2">
                <div className="grid grid-cols-2 gap-2 text-[10px]">
                  <div>
                    <p className="text-text-muted">Expected</p>
                    <pre className="font-mono text-text-primary bg-white rounded p-1.5 mt-0.5 overflow-x-auto max-h-32">
                      {JSON.stringify(r.expected, null, 2)}
                    </pre>
                  </div>
                  <div>
                    <p className="text-text-muted">Actual</p>
                    <pre className="font-mono text-text-primary bg-white rounded p-1.5 mt-0.5 overflow-x-auto max-h-32">
                      {JSON.stringify(r.actual, null, 2)}
                    </pre>
                  </div>
                </div>
                {Object.keys(r.metrics).length > 0 && (
                  <div>
                    <p className="text-[10px] text-text-muted">Metrics</p>
                    <div className="flex flex-wrap gap-2 mt-1">
                      {Object.entries(r.metrics).map(([k, v]) => (
                        <span key={k} className="px-1.5 py-0.5 rounded bg-accent/10 text-accent text-[10px] font-mono">
                          {k}: {typeof v === 'number' ? v.toFixed(3) : v}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {r.error_msg && (
                  <p className="text-[10px] text-red-500">{r.error_msg}</p>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
