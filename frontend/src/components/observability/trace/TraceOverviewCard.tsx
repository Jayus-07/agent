"use client";

import { TraceRecord } from "@/types/trace";

interface Props {
  trace: TraceRecord;
}

export default function TraceOverviewCard({ trace }: Props) {
  const hasError = trace.error && Object.keys(trace.error).length > 0;
  const spans = trace.spans || [];
  const llmCalls = spans.filter((s) => s.type === "llm_call" || s.llm_call).length;
  const toolCalls = spans.filter((s) =>
    s.type === "retrieval" || s.type === "rerank" || s.type === "tool_call"
  ).length;
  const totalTokens = trace.usage?.total_tokens ?? 0;

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
      {/* Trace ID */}
      <div className="bg-white border border-slate-200 rounded-xl p-4">
        <p className="text-[10px] uppercase tracking-widest text-slate-400 mb-1">Trace ID</p>
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-semibold text-slate-800">{trace.id.slice(0, 12)}</span>
          <button
            onClick={() => navigator.clipboard.writeText(trace.id)}
            className="text-slate-400 hover:text-slate-600"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="9" y="9" width="13" height="13" rx="2" />
              <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
            </svg>
          </button>
        </div>
        {trace.workflow_name && (
          <p className="text-[10px] text-slate-400 mt-0.5 font-mono">{trace.workflow_name}</p>
        )}
      </div>

      {/* Duration */}
      <div className="bg-white border border-slate-200 rounded-xl p-4">
        <p className="text-[10px] uppercase tracking-widest text-slate-400 mb-1">总耗时</p>
        <p className={`font-mono text-sm font-bold ${
          trace.duration_ms > 5000 ? "text-red-500" : trace.duration_ms > 2000 ? "text-amber-500" : "text-emerald-500"
        }`}>
          {trace.duration_ms}ms
        </p>
        <div className="mt-1.5 h-1.5 bg-slate-100 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${
              trace.duration_ms > 5000 ? "bg-red-400" : trace.duration_ms > 2000 ? "bg-amber-400" : "bg-emerald-400"
            }`}
            style={{ width: `${Math.min((trace.duration_ms / 10000) * 100, 100)}%` }}
          />
        </div>
      </div>

      {/* Status */}
      <div className="bg-white border border-slate-200 rounded-xl p-4">
        <p className="text-[10px] uppercase tracking-widest text-slate-400 mb-1">状态</p>
        <div className="flex items-center gap-2">
          <span className={`inline-block w-2.5 h-2.5 rounded-full ${hasError ? "bg-red-500" : "bg-emerald-500"}`} />
          <span className={`text-sm font-semibold ${hasError ? "text-red-600" : "text-emerald-600"}`}>
            {hasError ? "错误" : "成功"}
          </span>
        </div>
      </div>

      {/* Token */}
      <div className="bg-white border border-slate-200 rounded-xl p-4">
        <p className="text-[10px] uppercase tracking-widest text-slate-400 mb-1">Token 消耗</p>
        <p className="font-mono text-sm font-semibold text-slate-800">{totalTokens}</p>
        <p className="text-[10px] text-slate-400 mt-0.5">
          P:{trace.usage?.prompt_tokens ?? 0} C:{trace.usage?.completion_tokens ?? 0}
        </p>
      </div>

      {/* LLM Calls */}
      <div className="bg-white border border-slate-200 rounded-xl p-4">
        <p className="text-[10px] uppercase tracking-widest text-slate-400 mb-1">LLM 调用</p>
        <p className="font-mono text-sm font-semibold text-slate-800">{llmCalls}</p>
      </div>

      {/* Tool Calls */}
      <div className="bg-white border border-slate-200 rounded-xl p-4">
        <p className="text-[10px] uppercase tracking-widest text-slate-400 mb-1">工具调用</p>
        <p className="font-mono text-sm font-semibold text-slate-800">{toolCalls}</p>
      </div>
    </div>
  );
}
