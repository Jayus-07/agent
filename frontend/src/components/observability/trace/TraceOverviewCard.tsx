"use client";

import { TraceRecord, CS_TARGET_TO_EXPERT } from "@/types/trace";

/** CS 灰度标签行：放量组别 + 路由一致性一目了然 */
function CSBadgeRow({ trace }: { trace: TraceRecord }) {
  const tags = (trace.tags || {}) as Record<string, string>;
  const variant = tags.cs_variant;
  if (!variant) return null; // 非 CS 灰度 trace 不显示

  const target = tags.cs_target || "";
  const expertFinal = tags.cs_expert_final || "";
  const expectedExpert = CS_TARGET_TO_EXPERT[target] || "";
  // 预过滤 target 与实际派发 expert 不一致 = 路由问题，标红提示
  const routeMismatch = Boolean(expectedExpert && expertFinal && expectedExpert !== expertFinal);
  const handoff = tags.cs_handoff_state || "";

  return (
    <div className="col-span-2 md:col-span-4 lg:col-span-6 flex flex-wrap items-center gap-2">
      <span className={`text-[10px] font-semibold px-2 py-0.5 rounded ${
        variant === "treatment" ? "bg-violet-600 text-white" : "bg-slate-200 text-slate-600"
      }`}>
        CS {variant === "treatment" ? "Treatment" : "Control"}
      </span>
      {target && (
        <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-100 text-slate-600">
          路由 → {target}
        </span>
      )}
      {expertFinal && (
        <span className={`text-[10px] font-mono px-2 py-0.5 rounded ${
          routeMismatch ? "bg-red-100 text-red-700 font-semibold" : "bg-emerald-50 text-emerald-700"
        }`}
          title={routeMismatch ? `预过滤期望 ${expectedExpert}，实际派发 ${expertFinal}` : "路由一致"}
        >
          {routeMismatch ? "⚠ 路由不一致: " : "Expert: "}{expertFinal}
        </span>
      )}
      {handoff && (
        <span className={`text-[10px] font-mono px-2 py-0.5 rounded ${
          handoff.includes("handoff") || handoff.includes("human")
            ? "bg-amber-100 text-amber-700"
            : "bg-slate-100 text-slate-500"
        }`}>
          人工状态: {handoff}
        </span>
      )}
    </div>
  );
}

interface Props {
  trace: TraceRecord;
}

export default function TraceOverviewCard({ trace }: Props) {
  const hasError = trace.status === "error" || (trace.error && Object.keys(trace.error).length > 0);
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
      {/* CS 灰度标签行（仅 CS trace 显示） */}
      <CSBadgeRow trace={trace} />

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
