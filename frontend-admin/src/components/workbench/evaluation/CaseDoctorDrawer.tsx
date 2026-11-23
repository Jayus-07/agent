'use client'

import React from 'react'
import {
  RAG_CORE_METRICS,
  RAGAS_OPTIONAL_METRICS,
  formatScorePercent,
  getScoreLevel,
  getScoreTone,
  resolveMetricDef,
} from './ragMetrics'

export interface CaseDiagnosticData {
  caseId: string
  status: 'pass' | 'fail' | 'error' | 'skip'
  durationMs?: number
  question?: string
  contexts?: Array<{
    chunkId?: string
    fileName?: string
    text: string
    score?: number
  }>
  expectedAnswer?: string
  actualAnswer?: string
  metrics?: Record<string, number | null | undefined>
  errorMsg?: string
  errorStage?: string
}

export interface CaseDoctorDrawerProps {
  caseData: CaseDiagnosticData | null
  onClose: () => void
  onSaveReview?: (caseId: string, tag: string, reason: string) => void
}

export default function CaseDoctorDrawer({
  caseData,
  onClose,
  onSaveReview,
}: CaseDoctorDrawerProps) {
  if (!caseData) return null

  const isFailed = caseData.status === 'fail' || caseData.status === 'error'

  // 智能分析主要错因归因
  const diagnosticReason = (() => {
    if (caseData.errorMsg) return caseData.errorMsg
    const recall = caseData.metrics?.context_recall ?? caseData.metrics?.ragas_context_recall
    const faith = caseData.metrics?.faithfulness ?? caseData.metrics?.ragas_faithfulness
    const rel = caseData.metrics?.answer_relevance ?? caseData.metrics?.ragas_answer_relevancy

    if (recall !== undefined && recall !== null && recall < 0.6) {
      return '关键切片漏召回（检索阶段未命中核心依据）'
    }
    if (faith !== undefined && faith !== null && faith < 0.7) {
      return '回答包含无依据幻觉（生成阶段偏离检索事实）'
    }
    if (rel !== undefined && rel !== null && rel < 0.6) {
      return '回答偏离核心意图（Prompt 或推理未聚焦问题）'
    }
    if (isFailed) return '综合指标未达基线阈值'
    return '各项质量指标均达到基线标准'
  })()

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="评测用例深度诊断面板"
      className="fixed inset-0 z-50 flex justify-end bg-black/40 backdrop-blur-xs transition-opacity"
    >
      <div className="flex h-full w-full max-w-3xl flex-col bg-surface-base shadow-2xl">
        {/* 抽屉顶栏 */}
        <div className="flex items-center justify-between border-b border-border-subtle px-6 py-4">
          <div className="flex items-center gap-3">
            <span
              className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                caseData.status === 'pass'
                  ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                  : caseData.status === 'fail'
                  ? 'bg-rose-500/10 text-rose-600 dark:text-rose-400'
                  : 'bg-amber-500/10 text-amber-600 dark:text-amber-400'
              }`}
            >
              {caseData.status.toUpperCase()}
            </span>
            <div>
              <h3 className="font-mono text-sm font-bold text-text-primary">
                用例诊断 · {caseData.caseId}
              </h3>
              <p className="text-xs text-text-muted">
                耗时 {caseData.durationMs ? `${caseData.durationMs}ms` : '—'} · {diagnosticReason}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-text-muted hover:bg-bg-hover hover:text-text-primary"
            aria-label="关闭抽屉"
          >
            ✕
          </button>
        </div>

        {/* 抽屉内容主体 */}
        <div className="flex-1 space-y-5 overflow-y-auto p-6">
          {/* 用户提问 */}
          <div className="rounded-xl border border-border-subtle bg-bg-elevated p-4">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-text-muted">
              测试提问 (Question)
            </h4>
            <p className="mt-1.5 text-sm font-medium text-text-primary">
              {caseData.question || '（无显式输入内容）'}
            </p>
          </div>

          {/* 期望 vs 实际回答 */}
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="rounded-xl border border-emerald-500/20 bg-emerald-50/10 p-4 dark:bg-emerald-950/10">
              <h4 className="flex items-center gap-1.5 text-xs font-semibold text-emerald-700 dark:text-emerald-400">
                <span>✓</span>
                <span>期望参考答案 (Ground Truth)</span>
              </h4>
              <p className="mt-2 text-xs leading-relaxed text-text-secondary whitespace-pre-wrap">
                {caseData.expectedAnswer || '—'}
              </p>
            </div>

            <div
              className={`rounded-xl border p-4 ${
                isFailed
                  ? 'border-rose-500/20 bg-rose-50/10 dark:bg-rose-950/10'
                  : 'border-border-subtle bg-bg-elevated'
              }`}
            >
              <h4
                className={`flex items-center gap-1.5 text-xs font-semibold ${
                  isFailed ? 'text-rose-700 dark:text-rose-400' : 'text-text-primary'
                }`}
              >
                <span>⚡</span>
                <span>模型实际生成回答 (Actual Answer)</span>
              </h4>
              <p className="mt-2 text-xs leading-relaxed text-text-secondary whitespace-pre-wrap">
                {caseData.actualAnswer || '—'}
              </p>
            </div>
          </div>

          {/* 指标打分卡片 */}
          {caseData.metrics && Object.keys(caseData.metrics).length > 0 && (
            <div className="rounded-xl border border-border-subtle bg-surface-base p-4">
              <h4 className="text-xs font-semibold text-text-primary">
                评测指标与 Ragas 深度评分拆解
              </h4>
              <div className="mt-3 grid gap-2.5 sm:grid-cols-2">
                {Object.entries(caseData.metrics).map(([key, val]) => {
                  const def = resolveMetricDef(key)
                  const score = typeof val === 'number' ? val : null
                  const level = getScoreLevel(score, def?.defaultThreshold ?? 0.8)
                  const tone = getScoreTone(level)

                  return (
                    <div
                      key={key}
                      className="flex items-center justify-between rounded-lg border border-border-subtle bg-bg-elevated p-2 text-xs"
                    >
                      <div>
                        <p className="font-medium text-text-primary">
                          {def?.label || key}
                        </p>
                        {def?.enLabel && (
                          <p className="text-[10px] text-text-muted">{def.enLabel}</p>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs font-bold text-text-primary">
                          {formatScorePercent(score)}
                        </span>
                        <span
                          className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                            tone === 'good'
                              ? 'bg-emerald-500/10 text-emerald-600'
                              : tone === 'warn'
                              ? 'bg-amber-500/10 text-amber-600'
                              : 'bg-rose-500/10 text-rose-600'
                          }`}
                        >
                          {level}
                        </span>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* 召回知识库上下文切片 */}
          <div className="rounded-xl border border-border-subtle bg-surface-base p-4">
            <div className="flex items-center justify-between">
              <h4 className="text-xs font-semibold text-text-primary">
                检索召回切片 (Retrieved Contexts - {caseData.contexts?.length ?? 0} 个)
              </h4>
              <span className="text-[11px] text-text-muted">按相似度相关性排序</span>
            </div>
            <div className="mt-3 space-y-2.5">
              {(!caseData.contexts || caseData.contexts.length === 0) ? (
                <p className="text-xs text-text-muted">未召回到任何上下文切片。</p>
              ) : (
                caseData.contexts.map((ctx, idx) => (
                  <div
                    key={idx}
                    className="rounded-lg border border-border-subtle bg-bg-elevated p-3 text-xs"
                  >
                    <div className="flex items-center justify-between text-[11px] text-text-muted">
                      <span className="font-medium text-text-secondary">
                        #{idx + 1} {ctx.fileName || '知识切片'}
                      </span>
                      {ctx.score !== undefined && (
                        <span className="font-mono">相关度分数: {ctx.score.toFixed(3)}</span>
                      )}
                    </div>
                    <p className="mt-1.5 text-xs leading-relaxed text-text-primary whitespace-pre-wrap">
                      {ctx.text}
                    </p>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>

        {/* 抽屉底部操作 */}
        <div className="flex items-center justify-between border-t border-border-subtle px-6 py-3.5 bg-bg-elevated">
          <div className="flex items-center gap-2">
            <span className="text-xs text-text-muted">快速人工归因：</span>
            <button
              type="button"
              onClick={() => onSaveReview?.(caseData.caseId, 'retrieval_miss', '检索召回不足')}
              className="rounded-md border border-border-subtle bg-surface-base px-2 py-1 text-xs text-text-secondary hover:bg-bg-hover"
            >
              标为切片召回缺失
            </button>
            <button
              type="button"
              onClick={() => onSaveReview?.(caseData.caseId, 'prompt_issue', 'Prompt/模型未约束')}
              className="rounded-md border border-border-subtle bg-surface-base px-2 py-1 text-xs text-text-secondary hover:bg-bg-hover"
            >
              标为模型生成偏差
            </button>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg bg-text-primary px-4 py-1.5 text-xs font-medium text-bg-root transition-opacity hover:opacity-90"
          >
            完成
          </button>
        </div>
      </div>
    </div>
  )
}
