"use client";

import { CS_TARGET_TO_EXPERT, type TraceRecord } from "@/types/trace";
import { getRunStatus, getRunStatusLabel, getRunStatusTone } from "@/lib/observability/runtimeStatus";

const CS_HANDOFF_LABELS: Record<string, string> = {
  ai_active: "AI 处理中",
  handoff_requested: "请求人工",
  waiting_human: "等待人工",
  agent_offered: "已分配客服",
  human_active: "人工处理中",
  closed: "已关闭",
};

function CSRouteDiagnostics({ trace }: Props) {
  const tags = trace.tags ?? {};
  const variant = tags.cs_variant === "treatment" || tags.cs_variant === "control"
    ? tags.cs_variant
    : null;
  const target = Object.prototype.hasOwnProperty.call(CS_TARGET_TO_EXPERT, tags.cs_target)
    ? tags.cs_target
    : null;
  const expert = Object.values(CS_TARGET_TO_EXPERT).includes(tags.cs_expert_final || "")
    ? tags.cs_expert_final
    : null;
  const handoff = CS_HANDOFF_LABELS[tags.cs_handoff_state] || null;
  const expectedExpert = target ? CS_TARGET_TO_EXPERT[target] : null;
  const routeMismatch = Boolean(expectedExpert && expert && expectedExpert !== expert);

  if (!variant && !target && !expert && !handoff) return null;

  return (
    <div className="col-span-2 flex flex-wrap items-center gap-2 md:col-span-4 lg:col-span-6" aria-label="客服路由诊断">
      {variant && (
        <span className={`rounded px-2 py-0.5 text-[10px] font-semibold ${variant === "treatment" ? "bg-violet-600 text-white" : "bg-slate-200 text-slate-700"}`}>
          CS {variant === "treatment" ? "Treatment" : "Control"}
        </span>
      )}
      {target && (
        <span className="rounded bg-slate-100 px-2 py-0.5 font-mono text-[10px] text-slate-700">
          路由 → {target}
        </span>
      )}
      {expert && (
        <span className={`rounded px-2 py-0.5 font-mono text-[10px] ${routeMismatch ? "bg-red-100 font-semibold text-red-800" : "bg-emerald-50 text-emerald-800"}`}>
          {routeMismatch ? "⚠ 路由不一致 · " : expectedExpert ? "路由一致 · " : "实际 Expert · "}{expert}
        </span>
      )}
      {handoff && (
        <span className={`rounded px-2 py-0.5 text-[10px] ${tags.cs_handoff_state.includes("handoff") || tags.cs_handoff_state.includes("human") ? "bg-amber-100 text-amber-800" : "bg-slate-100 text-slate-600"}`}>
          人工状态：{handoff}
        </span>
      )}
    </div>
  );
}

interface Props {
  trace: TraceRecord;
}

export default function TraceOverviewCard({ trace }: Props) {
  const runStatus = getRunStatus(trace);
  const spans = trace.spans || [];

  // P1-5: 优先用后端 DTO summary（单一口径）；旧数据无 summary 时前端兜底
  const summary = (trace as unknown as { summary?: { llm_calls: number; tool_calls: number; retrieval_calls: number; span_count: number } }).summary;
  const llmCalls = summary?.llm_calls ?? spans.filter((s) => s.type === "llm_call" || s.llm_call).length;
  const toolCalls = summary?.tool_calls ?? spans.filter((s) => s.type === "tool_call").length;
  const retrievalCalls = summary?.retrieval_calls
    ?? spans.filter((s) => s.type === "retrieval" || s.type === "rerank").length;

  // P0-2: 区分"真 0"与"未采集"——span 指标或 usage 任一有值才算采集到
  const totalTokens = trace.usage?.total_tokens ?? 0;
  const tokensCollected = totalTokens > 0
    || spans.some((s) => Number(s.metrics?.total_tokens ?? 0) > 0);

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
      <CSRouteDiagnostics trace={trace} />
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
        <span className={`inline-flex rounded border px-2 py-1 text-xs font-semibold ${getRunStatusTone(runStatus)}`}>
          {getRunStatusLabel(runStatus)}
        </span>
      </div>

      {/* Token */}
      <div className="bg-white border border-slate-200 rounded-xl p-4">
        <p className="text-[10px] uppercase tracking-widest text-slate-400 mb-1">Token 消耗</p>
        {tokensCollected ? (
          <>
            <p className="font-mono text-sm font-semibold text-slate-800">{totalTokens}</p>
            <p className="text-[10px] text-slate-400 mt-0.5">
              P:{trace.usage?.prompt_tokens ?? 0} C:{trace.usage?.completion_tokens ?? 0}
            </p>
          </>
        ) : (
          <p className="text-xs text-slate-400 mt-1" title="该 trace 未采集到 token 用量（llm_usage 明细缺失）">
            未采集
          </p>
        )}
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

      {/* Retrieval Calls */}
      <div className="bg-white border border-slate-200 rounded-xl p-4">
        <p className="text-[10px] uppercase tracking-widest text-slate-400 mb-1">检索 / 重排</p>
        <p className="font-mono text-sm font-semibold text-slate-800">{retrievalCalls}</p>
      </div>
    </div>
  );
}
