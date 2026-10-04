'use client'

import { useEffect, useState, useCallback, useMemo } from 'react'
import { RefreshCw, ChevronDown, ChevronRight, CheckCircle2, XCircle, AlertCircle, SkipForward, PlayCircle, Download, Ban } from 'lucide-react'
import { evaluationService, type RunSummary, type EvalRunDetail } from '@/api/evaluation'
import CaseSampleDetail from '@/components/evaluations/CaseSampleDetail'
import { useToast } from '@/components/shared/Toast'
import EmptyState from '@/components/shared/EmptyState'
import dynamic from 'next/dynamic'

/** UI-01/04：运行状态徽标（running 超龄标 stale 疑似 worker 丢失） */
function RunStatusBadge({ status, stale }: { status?: string; stale?: boolean }) {
  if (!status) {
    return <span className="px-1.5 py-0.5 rounded text-[10px] bg-gray-100 text-gray-500 shrink-0">无状态</span>
  }
  if (status === 'running' && stale) {
    return <span className="px-1.5 py-0.5 rounded text-[10px] bg-amber-50 text-amber-700 shrink-0">running · stale?</span>
  }
  const style = status === 'completed'
    ? 'bg-green-50 text-green-700'
    : status === 'running'
      ? 'bg-blue-50 text-blue-700'
      : 'bg-red-50 text-red-700'
  return <span className={`px-1.5 py-0.5 rounded text-[10px] ${style} shrink-0`}>{status}</span>
}

/** UI-04：RAGAS 徽标——区分 双轨(self+ragas) / 仅自研(self) */
function RagasBadge({ mode }: { mode?: string }) {
  if (mode === 'self+ragas') {
    return <span className="px-1.5 py-0.5 rounded text-[10px] bg-violet-50 text-violet-700 shrink-0">RAGAS</span>
  }
  if (mode === 'self') {
    return <span className="px-1.5 py-0.5 rounded text-[10px] bg-gray-100 text-gray-500 shrink-0">仅自研</span>
  }
  return null
}

// recharts 较重，拆为独立 chunk 懒加载，避免路由切换时阻塞渲染
const TrendCharts = dynamic(() => import('@/app/evaluations/TrendCharts'), {
  ssr: false,
  loading: () => <div className="h-48 rounded-xl bg-black/5 animate-pulse" />,
})

const STATUS_ICON = {
  pass: <CheckCircle2 size={14} className="text-green-500" />,
  fail: <XCircle size={14} className="text-red-500" />,
  error: <AlertCircle size={14} className="text-orange-500" />,
  skip: <SkipForward size={14} className="text-gray-400" />,
}

export default function EvaluationResultsPanel() {
  const toast = useToast()
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [running, setRunning] = useState(false)
  const [loading, setLoading] = useState(true)
  const [selectedRun, setSelectedRun] = useState<string | null>(null)
  const [runDetail, setRunDetail] = useState<EvalRunDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [expandedCase, setExpandedCase] = useState<string | null>(null)
  // UI-02：状态/评测集/触发者筛选（客户端过滤，数据源 = 已加载的 runs）
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [suiteFilter, setSuiteFilter] = useState<string>('')
  const [triggerFilter, setTriggerFilter] = useState<string>('')

  const filteredRuns = useMemo(() => runs.filter(r => {
    if (statusFilter !== 'all' && (r.status || '') !== statusFilter) return false
    if (suiteFilter && !(r.suite || '').toLowerCase().includes(suiteFilter.toLowerCase())) return false
    if (triggerFilter && !`${r.trigger || ''} ${r.triggered_by || ''}`.toLowerCase().includes(triggerFilter.toLowerCase())) return false
    return true
  }), [runs, statusFilter, suiteFilter, triggerFilter])

  /** UI-08：导出当前筛选结果（JSON / CSV） */
  const handleExport = (format: 'json' | 'csv') => {
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')
    const triggerDownload = (blob: Blob, name: string) => {
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = name
      a.click()
      URL.revokeObjectURL(url)
    }
    if (format === 'json') {
      triggerDownload(
        new Blob([JSON.stringify(filteredRuns, null, 2)], { type: 'application/json' }),
        `eval-runs-${stamp}.json`,
      )
      return
    }
    const header = ['run_id', 'status', 'stale', 'suite', 'dataset_version', 'trigger', 'triggered_by', 'evaluator_mode', 'pass_rate', 'top1_accuracy', 'recall_at_5', 'mrr', 'ndcg_at_10', 'reject_accuracy', 'timestamp']
    const rows = filteredRuns.map(r => [
      r.run_id, r.status || '', String(r.stale ?? ''), r.suite || '', r.dataset_version || '',
      r.trigger || '', r.triggered_by || '', r.evaluator_mode || '',
      String(r.pass_rate), String(r.top1_accuracy), String(r.recall_at_5),
      String(r.mrr), String(r.ndcg_at_10), String(r.reject_accuracy), r.timestamp,
    ])
    const csv = [header, ...rows].map(cells => cells.map(c => `"${c.replace(/"/g, '""')}"`).join(',')).join('\n')
    triggerDownload(
      new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' }),
      `eval-runs-${stamp}.csv`,
    )
  }

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

  const handleRunEval = async () => {
    if (!confirm('发起 RAG 评测？（离线 smoke 口径，完成后自动刷新列表）')) return
    setRunning(true)
    try {
      const result = await evaluationService.runEval('rag')
      toast.success(`评测完成：通过率 ${(result.pass_rate * 100).toFixed(1)}%`)
      await loadRuns()
    } catch (e) {
      toast.error(`评测失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setRunning(false)
    }
  }

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

  /** C2-1/RUN-03：取消运行中评测（幂等；完成后刷新状态） */
  const handleCancelRun = async (runId: string) => {
    if (!confirm(`确认取消评测 ${runId}？已完成样本保留，剩余样本记 skip。`)) return
    try {
      const result = await evaluationService.cancelRun(runId)
      if (result.audit_recorded === false) {
        toast.error('取消请求已登记，但审计写入失败——请联系管理员核查')
        return
      }
      toast.success(result.already_cancelled ? '该运行已在取消流程中' : '取消请求已登记，剩余样本将停止执行')
      await loadRuns()
      const detail = await evaluationService.getRun(runId)
      setRunDetail(detail)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '取消失败')
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
      <div className="max-w-6xl mx-auto px-6 py-8">
        {/* 操作 */}
        <div className="flex items-center justify-end mb-6">
          <div className="flex items-center gap-2">
            <button
              onClick={handleRunEval}
              disabled={running}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors disabled:opacity-50"
            >
              <PlayCircle size={14} />
              {running ? '评测执行中…' : '发起 RAG 评测'}
            </button>
            <button
              onClick={loadRuns}
              disabled={loading}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-border-subtle text-text-secondary hover:text-text-primary transition-colors disabled:opacity-50"
            >
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
              刷新
            </button>
            <button
              onClick={() => handleExport('json')}
              disabled={filteredRuns.length === 0}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-border-subtle text-text-secondary hover:text-text-primary transition-colors disabled:opacity-50"
            >
              <Download size={14} />
              JSON
            </button>
            <button
              onClick={() => handleExport('csv')}
              disabled={filteredRuns.length === 0}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-border-subtle text-text-secondary hover:text-text-primary transition-colors disabled:opacity-50"
            >
              <Download size={14} />
              CSV
            </button>
          </div>
        </div>

        {/* UI-02：筛选栏 */}
        <div className="flex flex-wrap items-center gap-2 mb-4">
          <select
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value)}
            className="px-2 py-1.5 text-xs rounded-lg border border-border-subtle bg-surface-base text-text-secondary"
          >
            <option value="all">全部状态</option>
            <option value="completed">completed</option>
            <option value="running">running</option>
            <option value="failed">failed</option>
          </select>
          <input
            value={suiteFilter}
            onChange={e => setSuiteFilter(e.target.value)}
            placeholder="按评测集筛选（suite）"
            className="px-2 py-1.5 text-xs rounded-lg border border-border-subtle bg-surface-base text-text-secondary w-48"
          />
          <input
            value={triggerFilter}
            onChange={e => setTriggerFilter(e.target.value)}
            placeholder="按触发来源/触发者筛选"
            className="px-2 py-1.5 text-xs rounded-lg border border-border-subtle bg-surface-base text-text-secondary w-52"
          />
          <span className="text-[10px] text-text-muted">
            {filteredRuns.length}/{runs.length} 条
          </span>
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
          ) : filteredRuns.length === 0 ? (
            <div className="p-6">
              <EmptyState
                title="无匹配记录"
                description="当前筛选条件下没有运行记录，请调整状态/评测集/触发者筛选"
              />
            </div>
          ) : (
            <div className="divide-y divide-border-subtle">
              {filteredRuns.map(run => (
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
                      <div className="flex items-center gap-1.5">
                        <p className="text-xs font-medium text-text-primary font-mono">{run.run_id}</p>
                        <RunStatusBadge status={run.status} stale={run.stale} />
                        <RagasBadge mode={run.evaluator_mode} />
                      </div>
                      <p className="text-[10px] text-text-muted mt-0.5">
                        {run.timestamp ? new Date(run.timestamp).toLocaleString('zh-CN') : '-'}
                        {run.suite ? ` · ${run.suite}` : ''}
                        {run.trigger ? ` · ${run.trigger}${run.triggered_by ? `/${run.triggered_by}` : ''}` : ''}
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
                        <div className="space-y-2">
                          {/* RUN-01/07/08 + UI-08 + RUN-03：状态、导出、取消 */}
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2 text-[10px] text-text-muted">
                              <RunStatusBadge status={runDetail.run_status?.status} stale={runDetail.run_status?.stale} />
                              {runDetail.run_status?.started_at && (
                                <span>started {new Date(runDetail.run_status.started_at).toLocaleString('zh-CN')}</span>
                              )}
                              {runDetail.run_status?.finished_at && (
                                <span>→ finished {new Date(runDetail.run_status.finished_at).toLocaleString('zh-CN')}</span>
                              )}
                              {runDetail.run_status?.error && (
                                <span className="text-red-500">{runDetail.run_status.error}</span>
                              )}
                            </div>
                            <div className="flex items-center gap-1.5">
                              {runDetail.run_status?.status === 'running' && (
                                <button
                                  onClick={() => handleCancelRun(runDetail.run_id)}
                                  className="flex items-center gap-1 px-2 py-1 rounded border border-red-200 text-[10px] text-red-600 hover:bg-red-50 transition-colors"
                                >
                                  <Ban size={10} />
                                  取消运行
                                </button>
                              )}
                              <button
                                onClick={() => {
                                  const blob = new Blob([JSON.stringify(runDetail, null, 2)], { type: 'application/json' })
                                  const url = URL.createObjectURL(blob)
                                  const a = document.createElement('a')
                                  a.href = url
                                  a.download = `eval-report-${runDetail.run_id}.json`
                                  a.click()
                                  URL.revokeObjectURL(url)
                                }}
                                className="flex items-center gap-1 px-2 py-1 rounded border border-border-subtle text-[10px] text-text-secondary hover:text-text-primary transition-colors"
                              >
                                <Download size={10} />
                                导出报告 JSON
                              </button>
                            </div>
                          </div>
                          <CaseDetailList
                            detail={runDetail}
                            expandedCase={expandedCase}
                            onToggleCase={setExpandedCase}
                          />
                        </div>
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
      {/* Tier 汇总（含 GATE-12 样本量门原因） */}
      {tier_summaries.length > 0 && (
        <div className="flex flex-wrap gap-3 mb-3">
          {tier_summaries.map(ts => (
            <div
              key={ts.tier}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] ${
                ts.passed_threshold ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'
              }`}
              title={(ts.gate_reasons || []).join('；')}
            >
              {ts.passed_threshold ? <CheckCircle2 size={10} /> : <XCircle size={10} />}
              {ts.tier}: {(ts.pass_rate * 100).toFixed(0)}% (阈值 {(ts.threshold * 100).toFixed(0)}%)
              {ts.passed_min_samples === false && ' · 样本不足'}
            </div>
          ))}
        </div>
      )}

      {/* Case 列表（C8：行=状态+用例+耗时；展开=结构化证据链） */}
      <div className="space-y-1">
        {results.map(r => (
          <div key={r.case_id} className="border border-border-subtle rounded-lg overflow-hidden">
            <button
              onClick={() => onToggleCase(expandedCase === r.case_id ? null : r.case_id)}
              className="w-full flex items-center gap-2 px-3 py-2 hover:bg-black/[0.02] transition-colors text-left"
            >
              {STATUS_ICON[r.status]}
              <span className="text-xs font-mono text-text-primary flex-1">{r.case_id}</span>
              {/* C8-4：失败阶段徽标 */}
              {r.error_stage && (
                <span className="px-1.5 py-0.5 rounded text-[10px] bg-orange-50 text-orange-700">{r.error_stage}</span>
              )}
              {/* C8-3：RAGAS 分值 chips（列表行摘要） */}
              {(() => {
                const ragasKeys = Object.entries(r.metrics || {}).filter(([k, v]) => k.startsWith('ragas_') && k !== 'ragas_reason' && typeof v === 'number')
                return ragasKeys.length > 0 ? (
                  <span className="px-1.5 py-0.5 rounded text-[10px] bg-violet-50 text-violet-700">
                    RAGAS {ragasKeys.length}/4
                  </span>
                ) : null
              })()}
              <span className="text-[10px] text-text-muted">{r.duration_ms}ms</span>
            </button>
            {expandedCase === r.case_id && (
              <div className="px-3 py-2 bg-black/[0.01] border-t border-border-subtle">
                <CaseSampleDetail result={r} />
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
