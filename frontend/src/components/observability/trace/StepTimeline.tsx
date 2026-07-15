"use client";

import { TraceStep, stepColor } from "@/types/trace";

// -------- helpers --------

function safeNum(v: unknown, fallback = "--"): string {
  if (v === undefined || v === null) return fallback;
  const n = Number(v);
  return isNaN(n) ? fallback : String(n);
}

function safeStr(v: unknown, fallback = "--"): string {
  if (v === undefined || v === null || v === "") return fallback;
  return String(v);
}

// -------- per-step metrics --------

function StepMetrics({ step }: { step: TraceStep }) {
  const m = step.metrics;
  switch (step.id) {
    case "hybrid_retrieval":
      return <span className="text-[11px] text-slate-500">BM25:{safeNum(m.bm25_hits)} | 向量:{safeNum(m.vector_hits)} | 合并:{safeNum(m.merged_hits)}</span>;
    case "rerank":
      return (
        <span className={`text-[11px] ${Number(m.output_docs ?? 1) === 0 ? "text-red-500 font-semibold" : "text-slate-500"}`}>
          输入 {safeNum(m.input_docs)} → 输出 {safeNum(m.output_docs)} (阈值 {safeNum(m.threshold)})
        </span>
      );
    case "llm_generate":
      return <span className="text-[11px] text-slate-500">P:{safeNum(m.prompt_tokens)} C:{safeNum(m.completion_tokens)} T:{safeNum(m.total_tokens)}</span>;
    case "faithfulness":
      return (
        <span className={`text-[11px] ${Number(m.score) === 1 && Number(m.claims) === 0 ? "text-slate-400" : "text-emerald-600"}`}>
          得分: {typeof m.score === "number" ? m.score.toFixed(2) : "--"} ({safeNum(m.supported)}/{safeNum(m.claims)})
        </span>
      );
    case "retrieval":
      return <span className="text-[11px] text-slate-500">召回 {safeNum(m.retrieved_chunks)} chunks</span>;
    case "query_rewrite":
      return <span className="text-[11px] text-slate-500">变体: {safeNum(m.variants)}</span>;
    case "mq_check":
      return <span className="text-[11px] text-slate-500">触发:{String(m.triggered ?? false)} 模式:{safeStr(m.mode)}</span>;
    case "citation":
      return <span className="text-[11px] text-slate-500">验证:{safeNum(m.verified_citations)}/{safeNum(m.total_citations)}</span>;
    default:
      return null;
  }
}

// -------- main --------

interface Props {
  steps: TraceStep[];
  totalMs: number;
  onToggle?: (id: string) => void;
  expanded?: Set<string>;
}

export default function StepTimeline({ steps, totalMs, onToggle, expanded }: Props) {
  const maxMs = Math.max(...steps.map((s) => s.duration_ms), 1);

  return (
    <div className="space-y-1">
      {/* Header */}
      <div className="flex items-center gap-4 px-3 pb-2 mb-2 border-b border-slate-100 text-[10px] text-slate-400 uppercase tracking-wider">
        <span className="w-28 shrink-0">步骤</span>
        <span className="flex-1">耗时分布</span>
        <span className="w-24 shrink-0 text-right">耗时</span>
        <span className="w-56 shrink-0 hidden xl:block">关键指标</span>
      </div>

      {/* Rows */}
      {steps.map((step) => {
        const ratio = Math.max(step.duration_ratio * 100, step.status === "skipped" ? 0 : 0.3);
        const isSlowest = step.duration_ms === maxMs && step.duration_ms > 0;
        const isRerankZero = step.id === "rerank" && Number(step.metrics?.output_docs ?? -1) === 0;

        return (
          <div
            key={step.id}
            className={`group flex items-center gap-4 py-2.5 px-3 rounded-md transition-colors ${
              isRerankZero
                ? "bg-red-50 border border-red-100"
                : isSlowest
                ? "bg-amber-50/50"
                : "hover:bg-slate-50"
            }`}
          >
            {/* Label */}
            <div className="w-28 shrink-0 flex items-center gap-2">
              <span
                className={`inline-block w-1.5 h-1.5 rounded-full ${
                  step.status === "skipped" ? "bg-slate-300" : step.status === "error" ? "bg-red-400" : "bg-violet-500"
                }`}
              />
              <span className={`text-xs ${step.status === "skipped" ? "text-slate-400 line-through" : "text-slate-700"}`}>
                {step.label}
              </span>
            </div>

            {/* Bar */}
            <div className="flex-1 h-6 bg-slate-100 rounded-sm overflow-hidden relative">
              <div
                className={`h-full rounded-sm transition-all duration-300 ${stepColor(step.status, step.duration_ms)}`}
                style={{ width: `${Math.min(ratio, 100)}%` }}
              />
              {/* Percentage label */}
              {step.duration_ms > 0 && (
                <span className={`absolute inset-y-0 flex items-center text-[10px] font-mono tabular-nums ${ratio > 25 ? "text-white pl-2 drop-shadow-sm" : "text-slate-500"} `}
                  style={{ left: ratio > 25 ? "0" : `calc(${Math.min(ratio, 100)}% + 6px)` }}>
                  {(step.duration_ratio * 100).toFixed(0)}%
                </span>
              )}
            </div>

            {/* Duration number */}
            <div className="w-24 shrink-0 text-right">
              <span
                className={`text-xs font-mono tabular-nums ${
                  isSlowest ? "text-red-500 font-bold" : step.status === "skipped" ? "text-slate-400" : "text-slate-600"
                }`}
              >
                {step.duration_ms}ms
              </span>
            </div>

            {/* Metrics */}
            <div className="w-56 shrink-0 hidden xl:block">
              <StepMetrics step={step} />
            </div>
          </div>
        );
      })}

      {/* Legend */}
      <div className="flex items-center gap-4 px-3 pt-3 text-[10px] text-slate-400">
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-violet-500" /> 正常</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-amber-500" /> &gt;1s</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-slate-200 border border-dashed border-slate-300" /> 跳过</span>
        <span className="flex items-center gap-1 ml-auto">{steps.length} 步骤 · {totalMs}ms</span>
      </div>
    </div>
  );
}
