"use client";

import { useState } from "react";
import { TraceStep } from "@/types/trace";
import { formatCost } from "@/types/trace";

/**
 * LLM 调用明细：每步的 prompt/response/model/temperature
 */

interface Props {
  steps: TraceStep[];
}

export default function LLMCallDetail({ steps }: Props) {
  const llmSteps = steps.filter((s) => s.llm_call);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggle = (id: string) => {
    const next = new Set(expanded);
    next.has(id) ? next.delete(id) : next.add(id);
    setExpanded(next);
  };

  if (llmSteps.length === 0) {
    return (
      <div className="bg-white border border-slate-200 rounded-xl p-5 text-center text-xs text-slate-400">
        本次 trace 不包含 LLM 调用（可能因错误短路）
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {llmSteps.map((step) => {
        const call = step.llm_call!;
        const isOpen = expanded.has(step.id);
        return (
          <div key={step.id} className="bg-white border border-slate-200 rounded-xl overflow-hidden">
            {/* Header */}
            <button
              onClick={() => toggle(step.id)}
              className="w-full flex items-center gap-3 px-4 py-3 hover:bg-slate-50 transition-colors text-left"
            >
              <span className={`text-[10px] font-mono transform transition-transform ${isOpen ? "rotate-90" : ""}`}>▶</span>
              <span className="text-xs font-medium text-slate-700">{step.label}</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-violet-50 text-violet-700 font-mono">{call.model}</span>
              <span className="text-[10px] text-slate-400">T={call.temperature}</span>
              <div className="ml-auto flex items-center gap-3 text-[10px] text-slate-500 font-mono">
                <span>P:{call.prompt_tokens}</span>
                <span>C:{call.completion_tokens}</span>
                <span className="text-emerald-600 font-semibold">{formatCost(call.cost_usd)}</span>
              </div>
            </button>

            {/* Body */}
            {isOpen && (
              <div className="border-t border-slate-100 p-4 space-y-3 bg-slate-50/50">
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-slate-400 mb-1.5">Prompt</p>
                  <pre className="text-xs font-mono text-slate-700 bg-white border border-slate-200 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap leading-relaxed">{call.prompt_text}</pre>
                </div>
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-slate-400 mb-1.5">Response</p>
                  <pre className="text-xs font-mono text-slate-700 bg-white border border-slate-200 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap leading-relaxed">{call.response_text}</pre>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}