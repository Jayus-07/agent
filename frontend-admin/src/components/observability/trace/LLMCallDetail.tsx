"use client";

import { useState } from "react";
import { Span, formatCost } from "@/types/trace";
import { safeRuntimeLabel } from "@/lib/observability/runtimeStatus";

interface Props {
  steps: Span[];
}

function metricNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export default function LLMCallDetail({ steps }: Props) {
  const llmSteps = steps.filter((span) => span.type === "llm_call" || span.llm_call);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggle = (id: string) => {
    const next = new Set(expanded);
    next.has(id) ? next.delete(id) : next.add(id);
    setExpanded(next);
  };

  if (llmSteps.length === 0) {
    return <p className="rounded-md border border-slate-200 bg-white p-4 text-sm text-slate-500">本次 Trace 未记录 LLM 调用。</p>;
  }

  return (
    <div className="space-y-2">
      {llmSteps.map((span) => {
        const call = span.llm_call;
        const model = call?.model || (typeof span.attributes?.["llm.model"] === "string" ? String(span.attributes["llm.model"]) : null);
        // DTO 为兼容旧数据会把缺失 Token 填成 0；来源明确不可用时不能把占位值当实测用量。
        const tokenCollectionUnavailable = span.metrics.token_source === "unavailable";
        const promptTokens = tokenCollectionUnavailable
          ? null
          : metricNumber(span.metrics.prompt_tokens);
        const completionTokens = tokenCollectionUnavailable
          ? null
          : metricNumber(span.metrics.completion_tokens);
        const totalTokens = tokenCollectionUnavailable
          ? null
          : metricNumber(span.metrics.total_tokens)
            ?? (promptTokens !== null && completionTokens !== null ? promptTokens + completionTokens : null);
        const costCny = metricNumber(call?.cost_cny) ?? metricNumber(span.metrics.cost_cny);
        const isOpen = expanded.has(span.id);

        return (
          <section key={span.id} className="overflow-hidden rounded-md border border-slate-200 bg-white">
            <button
              type="button"
              aria-expanded={isOpen}
              onClick={() => toggle(span.id)}
              className="flex min-h-11 w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 text-left hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-600"
            >
              <span className="min-w-0 flex-1 text-sm font-medium text-slate-800">{safeRuntimeLabel(span.name, "LLM 调用")}</span>
              <span className="text-xs text-slate-600">{model || "模型未采集"}</span>
              <span className="font-mono text-xs text-slate-600">Token {totalTokens === null ? "未采集" : totalTokens}</span>
              <span className="font-mono text-xs text-slate-600">费用 {costCny === null ? "未采集" : formatCost(costCny)}</span>
            </button>
            {isOpen && (
              <div className="grid grid-cols-2 gap-3 border-t border-slate-100 bg-slate-50 p-4 text-xs sm:grid-cols-4">
                <div><span className="block text-slate-500">模型</span><span className="font-mono text-slate-800">{model || "未采集"}</span></div>
                <div><span className="block text-slate-500">Prompt Tokens</span><span className="font-mono text-slate-800">{promptTokens ?? "未采集"}</span></div>
                <div><span className="block text-slate-500">Completion Tokens</span><span className="font-mono text-slate-800">{completionTokens ?? "未采集"}</span></div>
                <div><span className="block text-slate-500">费用（CNY）</span><span className="font-mono text-slate-800">{costCny === null ? "未采集" : formatCost(costCny)}</span></div>
                <p className="col-span-full text-slate-500">Prompt 与响应文本已隐藏；仅显示实际采集的运行元数据。</p>
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
