/**
 * /tools「运行质量与失败」Tab 的 AI 失败分析区块（决策 #20）：
 * 时间窗失败聚类 → LLM 归因/建议/候选评测用例。（自 page.tsx 拆出，纯移动。）
 *
 * 边界：只生成建议与候选评测集，不自动应用/改写任何正式配置；送 LLM 的
 * 输入只有聚类计数与截断摘要，不含用户参数原文与凭据（后端保证）。
 */
import { useCallback, useEffect, useState } from 'react'
import { Loader2, ShieldCheck } from 'lucide-react'
import { AssetSection } from '@/components/layout/AssetPageShell'
import { useToast } from '@/components/shared/Toast'
import {
  getFailureAnalyses, runFailureAnalysis,
  type FailureAnalysisRecord, type FailureAnalysisResult,
} from '@/api/failureAnalysis'
import { errMessage } from '@/api/client'
import { ERROR_CLASS_LABEL } from './meta'

/** AI 失败分析时间窗（分钟）：短窗看即时故障，长窗看趋势。 */
const FAILURE_ANALYSIS_WINDOWS = [
  { value: 60, label: '最近 60 分钟' },
  { value: 360, label: '最近 6 小时' },
  { value: 1440, label: '最近 24 小时' },
]

/** AI 失败分析聚类卡：tool / 错误分类 / 归因 / 修复建议 / 候选评测用例。 */
function AnalysisClusterCard({ cluster }: { cluster: FailureAnalysisResult['clusters'][number] }) {
  const cases = cluster.candidate_eval_cases ?? []
  return (
    <div className="rounded-lg border border-border-subtle bg-surface-base p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[12px] font-medium text-text-primary">{cluster.tool}</span>
        <span className="rounded bg-red-100 px-1.5 py-0.5 text-[10px] font-medium text-red-700">
          {ERROR_CLASS_LABEL[cluster.error_class] ?? cluster.error_class}
        </span>
      </div>
      <div className="mt-1.5 text-[12px] leading-relaxed text-text-secondary">
        <span className="font-medium">归因：</span>{cluster.attribution || '—'}
      </div>
      {(cluster.suggestions?.length ?? 0) > 0 && (
        <div className="mt-1.5">
          <div className="text-[11px] font-medium text-text-muted">修复建议（仅建议）</div>
          <ul className="mt-0.5 list-disc space-y-0.5 pl-4 text-[12px] text-text-secondary">
            {cluster.suggestions.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
      )}
      {cases.length > 0 && (
        <div className="mt-2">
          <div className="text-[11px] font-medium text-text-muted">候选评测用例（仅建议——应用需走评测中心审核与回归）</div>
          <table className="mt-1 w-full text-[12px]">
            <thead>
              <tr className="text-left text-[11px] text-text-muted">
                <th className="py-1 pr-3 font-normal">评测问题</th>
                <th className="py-1 pr-3 font-normal">期望能力</th>
                <th className="py-1 font-normal">验证点</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-black/[0.04]">
              {cases.map((c, i) => (
                <tr key={i}>
                  <td className="py-1 pr-3 text-text-primary">{c.question}</td>
                  <td className="py-1 pr-3 font-mono text-[11px] text-text-secondary">{c.expected_capability || '—'}</td>
                  <td className="py-1 text-text-muted">{c.expectation_note || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export function FailureAnalysisSection() {
  const toast = useToast()
  const [windowMinutes, setWindowMinutes] = useState(60)
  const [running, setRunning] = useState(false)
  const [runError, setRunError] = useState<string | null>(null)
  const [result, setResult] = useState<FailureAnalysisResult | null>(null)
  const [recent, setRecent] = useState<FailureAnalysisRecord[]>([])
  const [recentLoading, setRecentLoading] = useState(true)

  const loadRecent = useCallback(() => {
    setRecentLoading(true)
    getFailureAnalyses(10)
      .then((r) => setRecent(r.analyses))
      .catch(() => setRecent([]))
      .finally(() => setRecentLoading(false))
  }, [])

  useEffect(() => { loadRecent() }, [loadRecent])

  const run = async () => {
    setRunning(true)
    setRunError(null)
    try {
      const r = await runFailureAnalysis({ window_minutes: windowMinutes })
      setResult(r)
      toast.success('分析完成')
      loadRecent()
    } catch (e) {
      setRunError(errMessage(e))
    } finally {
      setRunning(false)
    }
  }

  const viewRecord = (record: FailureAnalysisRecord) => {
    setResult({
      analysis_id: record.id,
      clusters: record.result?.clusters ?? [],
      created_at: record.created_at,
    })
    setRunError(null)
  }

  const clusters = result?.clusters ?? []

  return (
    <AssetSection title="AI 失败分析" meta="决策 #20 · 只生成建议，不自动生效" icon={<ShieldCheck size={15} />} bodyClassName="pt-3">
      <div className="space-y-3">
        <div className="text-[12px] leading-relaxed text-text-secondary">
          对时间窗内的 Tool 失败做 (工具 × 错误分类) 聚类，AI 产出归因分析、修复建议与候选评测用例。
          送入分析的只有聚类计数与截断错误摘要，<span className="font-medium">不含用户参数原文与凭据</span>；
          结果仅落管理端留痕，不自动改写正式 Prompt / 语义契约 / Golden 数据。
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            aria-label="分析时间窗"
            value={windowMinutes}
            onChange={(e) => setWindowMinutes(Number(e.target.value))}
            className="rounded-md border border-border-subtle bg-surface-base px-2.5 py-1.5 text-[12px] text-text-primary"
          >
            {FAILURE_ANALYSIS_WINDOWS.map((w) => <option key={w.value} value={w.value}>{w.label}</option>)}
          </select>
          <button
            type="button"
            onClick={() => void run()}
            disabled={running}
            className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-[12px] font-medium text-white hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {running && <Loader2 size={13} className="animate-spin" />}
            {running ? '生成中…' : '生成分析'}
          </button>
          {runError && <span className="text-[12px] text-red-700">{runError}</span>}
        </div>

        {result && (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2 text-[11px] text-text-muted">
              <span>分析 {result.analysis_id.slice(0, 16)}</span>
              <span>·</span>
              <span>{new Date(result.created_at).toLocaleString()}</span>
            </div>
            {clusters.length === 0 ? (
              <div className="rounded-lg border border-border-subtle bg-surface-base px-3 py-4 text-center text-[12px] text-text-muted">
                该时间窗内没有 Tool 失败记录（无失败可分析；可放大时间窗重试）
              </div>
            ) : (
              <div className="grid gap-2">
                {clusters.map((c, i) => <AnalysisClusterCard key={`${c.tool}-${c.error_class}-${i}`} cluster={c} />)}
              </div>
            )}
          </div>
        )}

        <div className="border-t border-border-subtle pt-2">
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-[12px] font-medium text-text-secondary">最近分析</span>
            {recentLoading && <span className="text-[11px] text-text-muted">加载中…</span>}
          </div>
          {!recentLoading && recent.length === 0 && (
            <div className="text-[12px] text-text-muted">暂无历史分析</div>
          )}
          {recent.length > 0 && (
            <ul className="space-y-1">
              {recent.map((a) => (
                <li key={a.id}>
                  <button
                    type="button"
                    onClick={() => viewRecord(a)}
                    className="flex w-full flex-wrap items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12px] text-text-secondary hover:bg-bg-hover"
                  >
                    <span className="font-mono text-[11px] text-text-muted">
                      {a.created_at.replace('T', ' ').slice(0, 19)}
                    </span>
                    <span>窗口 {a.window_minutes} 分钟</span>
                    <span>{a.cluster_count} 个聚类</span>
                    {a.model && <span className="font-mono text-[11px] text-text-muted">{a.model}</span>}
                    <span className="ml-auto text-[11px] text-accent">回看 →</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </AssetSection>
  )
}
