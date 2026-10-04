'use client'

import { useState } from 'react'
import {
  CheckCircle2, XCircle, AlertCircle, SkipForward, ChevronDown, ChevronRight,
  FileSearch, Quote, Bot, ExternalLink, ShieldAlert, Route,
} from 'lucide-react'
import type { EvalResultDetail } from '@/api/evaluation'

/**
 * C8 样本级证据链（2026-10-04 验收收敛八期）：
 * - C8-1/UI-05：分区结构化视图——检索命中表 / 最终上下文 / 生成答案 /
 *   RAGAS·judge 分数 chips；Expected/Actual 原始 JSON 折叠为「查看原文」
 * - C8-2/TRACE-09/EVD-09：trace_id → /observability/traces/{id} 跳转
 * - C8-3/RAGAS-12/EVD-07：RAGAS 指标独立区块（不与 sem_* 自研混用）+
 *   judge 四维明细独立展示
 * - C8-4/EVD-08：error_stage 徽标（retrieval/generation/judge/ragas/timeout/…）
 * - C8-5/EVD-02：router 决策证据（评测直连检索链时诚实标注 not_captured）
 */
export default function CaseSampleDetail({ result }: { result: EvalResultDetail }) {
  const [showRaw, setShowRaw] = useState(false)
  const actual = (result.actual || {}) as Record<string, unknown>
  const details = Array.isArray(actual.details) ? (actual.details as Record<string, unknown>[]) : []
  const trace = (actual.trace || {}) as Record<string, unknown>
  const traceId = typeof trace.trace_id === 'string' ? trace.trace_id : ''
  const router = (actual.router || null) as { captured?: boolean; reason?: string } | null
  const generatedAnswer = typeof actual.generated_answer === 'string' ? actual.generated_answer : ''

  const metricEntries = Object.entries(result.metrics || {})
  const ragasMetrics = metricEntries.filter(([k, v]) => k.startsWith('ragas_') && k !== 'ragas_reason' && typeof v === 'number')
  const judgeMetrics = metricEntries.filter(([k, v]) => k.startsWith('judge_') && typeof v === 'number')
  const selfMetrics = metricEntries.filter(([k, v]) => (k.startsWith('sem_') || k.startsWith('S6_') || k.startsWith('S7_')) && typeof v === 'number')
  const ragasReason = (result.metrics || {})['ragas_reason']

  return (
    <div className="space-y-2 text-[11px]">
      {/* C8-4：错误阶段徽标 + 错误消息 */}
      {result.error_stage && (
        <div className="flex items-center gap-1.5">
          <span className="px-1.5 py-0.5 rounded text-[10px] bg-orange-50 text-orange-700 inline-flex items-center gap-1">
            <ShieldAlert size={10} /> 失败阶段: {result.error_stage}
          </span>
        </div>
      )}

      {/* C8-5：router 决策证据（诚实口径：直连链路=未捕获） */}
      {router && (
        <div className="flex items-center gap-1 text-text-muted">
          <Route size={10} />
          <span>
            Router 决策: {router.captured ? '已捕获' : `未捕获（${router.reason || 'rag_eval_direct_no_router'}——评测直连 RAG 检索链，不经 router）`}
          </span>
        </div>
      )}

      {/* C8-2：trace 跳转（EVD-09） */}
      {traceId && (
        <a
          href={`/observability/traces/${encodeURIComponent(traceId)}`}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-accent hover:underline"
        >
          <ExternalLink size={10} />
          Trace: <span className="font-mono">{traceId}</span>
          {typeof trace.total_spans === 'number' ? ` · ${trace.total_spans} spans` : ''}
        </a>
      )}

      {/* C8-1：检索命中表（doc_id / 标题 / rerank 分） */}
      {details.length > 0 && (
        <div>
          <p className="text-text-muted flex items-center gap-1"><FileSearch size={10} /> 检索命中（{details.length}）</p>
          <div className="mt-1 overflow-x-auto rounded border border-border-subtle bg-white">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-border-subtle text-text-muted">
                  <th className="px-2 py-1 font-normal">#</th>
                  <th className="px-2 py-1 font-normal">doc_id / 标题</th>
                  <th className="px-2 py-1 font-normal">rerank 分</th>
                </tr>
              </thead>
              <tbody>
                {details.slice(0, 10).map((d, i) => (
                  <tr key={i} className="border-b border-border-subtle/60 last:border-0">
                    <td className="px-2 py-1 text-text-muted">{i + 1}</td>
                    <td className="px-2 py-1 font-mono truncate max-w-[320px]" title={String(d.title || d.doc_id || '')}>
                      {String(d.doc_id || '—')}{d.title ? ` · ${String(d.title)}` : ''}
                    </td>
                    <td className="px-2 py-1">
                      {typeof d.rerank_score === 'number' ? d.rerank_score.toFixed(4) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* C8-1：生成答案 */}
      {generatedAnswer && (
        <div>
          <p className="text-text-muted flex items-center gap-1"><Bot size={10} /> 生成答案</p>
          <p className="mt-1 rounded border border-border-subtle bg-white p-2 whitespace-pre-wrap max-h-32 overflow-y-auto">{generatedAnswer}</p>
        </div>
      )}

      {/* C8-3：RAGAS 独立区块（ragas_* 不与 sem_* 混用） */}
      {(ragasMetrics.length > 0 || ragasReason) && (
        <div className="rounded border border-violet-200 bg-violet-50/50 p-2">
          <p className="text-violet-700 font-medium">RAGAS 指标（独立口径）</p>
          <div className="flex flex-wrap gap-1.5 mt-1">
            {ragasMetrics.map(([k, v]) => (
              <span key={k} className="px-1.5 py-0.5 rounded bg-white border border-violet-200 text-violet-700 font-mono">
                {k.replace('ragas_', '')}: {(v as number).toFixed(4)}
              </span>
            ))}
            {ragasReason && (
              <span className="px-1.5 py-0.5 rounded bg-white border border-violet-200 text-violet-500">
                reason: {String(ragasReason)}
              </span>
            )}
          </div>
        </div>
      )}

      {/* C8-3：judge 明细独立区块 */}
      {judgeMetrics.length > 0 && (
        <div className="rounded border border-sky-200 bg-sky-50/50 p-2">
          <p className="text-sky-700 font-medium">Judge 评分（LLM-as-Judge 四维）</p>
          <div className="flex flex-wrap gap-1.5 mt-1">
            {judgeMetrics.map(([k, v]) => (
              <span key={k} className="px-1.5 py-0.5 rounded bg-white border border-sky-200 text-sky-700 font-mono">
                {k.replace('judge_', '')}: {((v as number) * 5).toFixed(2)}/5
              </span>
            ))}
          </div>
        </div>
      )}

      {/* 自研语义指标 chips（与 RAGAS 区块分离展示） */}
      {selfMetrics.length > 0 && (
        <div>
          <p className="text-text-muted">自研语义指标</p>
          <div className="flex flex-wrap gap-1.5 mt-1">
            {selfMetrics.map(([k, v]) => (
              <span key={k} className="px-1.5 py-0.5 rounded bg-accent/10 text-accent font-mono">
                {k}: {(v as number).toFixed(4)}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* 原始 JSON 折叠（C8-1：Expected/Actual 原文按需展开） */}
      <button
        onClick={() => setShowRaw(prev => !prev)}
        className="inline-flex items-center gap-1 text-text-muted hover:text-text-primary"
      >
        {showRaw ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
        查看原文（Expected / Actual JSON）
      </button>
      {showRaw && (
        <div className="grid grid-cols-2 gap-2">
          <div>
            <p className="text-text-muted">Expected</p>
            <pre className="font-mono text-text-primary bg-white rounded p-1.5 mt-0.5 overflow-x-auto max-h-32">
              {JSON.stringify(result.expected, null, 2)}
            </pre>
          </div>
          <div>
            <p className="text-text-muted">Actual</p>
            <pre className="font-mono text-text-primary bg-white rounded p-1.5 mt-0.5 overflow-x-auto max-h-32">
              {JSON.stringify(actual, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}

/** 样本状态图标（供列表行复用） */
export function ResultStatusIcon({ status }: { status: EvalResultDetail['status'] }) {
  const icons = {
    pass: <CheckCircle2 size={14} className="text-green-500" />,
    fail: <XCircle size={14} className="text-red-500" />,
    error: <AlertCircle size={14} className="text-orange-500" />,
    skip: <SkipForward size={14} className="text-gray-400" />,
  }
  return icons[status] ?? null
}

export function QuoteIcon() {
  return <Quote size={10} />
}
