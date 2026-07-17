"use client";

import { useState } from "react";
import { Span, spanColor, spanTypeColor, SPAN_TYPE_LABELS, safeNum, safeStr } from "@/types/trace";

// 注：safeNum / safeStr 已统一在 @/types/trace.ts 导出，避免重复实现。

// -------- 动态指标渲染（按 span type） --------

function SpanMetrics({ span }: { span: Span }) {
  const m = span.metrics;
  const id = span.id;

  if (span.type === "retrieval") {
    if (m.bm25_hits !== undefined || m.vector_hits !== undefined) {
      return <span className="text-[11px] text-slate-500">BM25:{safeNum(m.bm25_hits)} | 向量:{safeNum(m.vector_hits)} | 合并:{safeNum(m.merged_hits)}</span>;
    }
    return <span className="text-[11px] text-slate-500">召回 {safeNum(m.retrieved_chunks)} chunks</span>;
  }
  if (span.type === "rerank") {
    return <span className={`text-[11px] ${Number(m.output_docs ?? 1) === 0 ? "text-red-500 font-semibold" : "text-slate-500"}`}>输入 {safeNum(m.input_docs)} → 输出 {safeNum(m.output_docs)} (阈值 {safeNum(m.threshold)})</span>;
  }
  if (span.type === "llm_call") {
    return <span className="text-[11px] text-slate-500">P:{safeNum(m.prompt_tokens)} C:{safeNum(m.completion_tokens)} T:{safeNum(m.total_tokens)}</span>;
  }
  if (span.type === "tool_call") {
    if (id === "faithfulness" || span.name === "Faithfulness") return <span className={`text-[11px] ${Number(m.score) === 1 && Number(m.claims) === 0 ? "text-slate-400" : "text-emerald-600"}`}>得分: {typeof m.score === "number" ? m.score.toFixed(2) : "--"} ({safeNum(m.supported)}/{safeNum(m.claims)})</span>;
    if (id === "mq_check" || span.name === "MultiQuery") return <span className="text-[11px] text-slate-500">触发:{String(m.triggered ?? false)} 模式:{safeStr(m.mode)}</span>;
    if (id === "citation" || span.name === "Citation") return <span className="text-[11px] text-slate-500">验证:{safeNum(m.verified_citations)}/{safeNum(m.total_citations)}</span>;
  }
  const typeLabel = SPAN_TYPE_LABELS[span.type] || span.type;
  const keyCount = Object.keys(m).length;
  if (keyCount > 0) {
    const firstKey = Object.keys(m)[0];
    return <span className="text-[11px] text-slate-400">{typeLabel} · {firstKey}: {safeNum(m[firstKey])}</span>;
  }
  return <span className="text-[11px] text-slate-400">{typeLabel}</span>;
}

// -------- Per-Span Raw JSON (内联展开) --------

function SpanJsonPanel({ span }: { span: Span }) {
  const [copied, setCopied] = useState(false);
  const json = JSON.stringify(span, null, 2);

  const handleCopy = () => {
    navigator.clipboard.writeText(json);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="border-t border-slate-100 bg-slate-50/50 px-4 py-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-[9px] uppercase tracking-wider text-slate-400">{SPAN_TYPE_LABELS[span.type] || span.type} Raw JSON</span>
          <span className={`inline-block w-2 h-2 rounded ${spanTypeColor(span.type)}`} />
        </div>
        <div className="flex items-center gap-1">
          <button onClick={handleCopy} className="text-[10px] text-slate-500 hover:text-slate-700 border border-slate-200 rounded px-2 py-0.5 transition-colors">
            {copied ? "✓ 已复制" : "📋 复制"}
          </button>
        </div>
      </div>
      <pre className="text-[10px] font-mono text-slate-600 bg-white border border-slate-200 rounded-lg p-3 overflow-x-auto max-h-80 overflow-y-auto leading-relaxed whitespace-pre">
        {json}
      </pre>
    </div>
  );
}

// -------- main --------

interface Props {
  steps: Span[];
  totalMs: number;
  onToggle?: (id: string) => void;
  expanded?: Set<string>;
  jsonExpanded?: Set<string>;
  onJsonToggle?: (id: string) => void;
  highlightStepId?: string | null;
}

export default function StepTimeline({ steps, totalMs, onToggle, expanded, jsonExpanded, onJsonToggle, highlightStepId }: Props) {
  const maxMs = Math.max(...steps.map((s) => s.duration_ms), 1);

  // 判断 kind 是否属于 LangGraph 特殊节点
  const isRound = (s: Span) => (s as any).kind === "graph_loop" || (s as any).kind === "graph_fallback";
  const isRoute = (s: Span) => (s as any).kind === "graph_route";
  const isFallback = (s: Span) => (s as any).kind === "graph_fallback";

  // 计算嵌套深度（子 span 渲染时用）
  const depthMap = new Map<string, number>();
  for (const s of steps) {
    let d = 0;
    let cur: Span | undefined = s;
    while (cur && cur.parent_id) {
      cur = steps.find((x) => x.id === cur!.parent_id || (x as any).span_id === cur!.parent_id);
      if (cur) d++;
      else break;
    }
    depthMap.set(s.id, d);
  }

  return (
    <div className="space-y-1">
      {/* Header */}
      <div className="flex items-center gap-4 px-3 pb-2 mb-2 border-b border-slate-100 text-[10px] text-slate-400 uppercase tracking-wider">
        <span className="w-28 shrink-0">步骤</span>
        <span className="flex-1">耗时分布</span>
        <span className="w-24 shrink-0 text-right">耗时</span>
        <span className="w-56 shrink-0 hidden xl:block">关键指标</span>
        <span className="w-10 shrink-0"></span>
      </div>

      {steps.map((span) => {
        const ratio = Math.max(span.duration_ratio * 100, span.status === "skipped" ? 0 : 0.3);
        const isSlowest = span.duration_ms === maxMs && span.duration_ms > 0;
        const isRerankZero = span.type === "rerank" && Number(span.metrics?.output_docs ?? -1) === 0;
        const isHighlight = highlightStepId === span.id;
        const isJsonOpen = jsonExpanded?.has(span.id);
        const depth = depthMap.get(span.id) || 0;
        const attrs = (span as any).attributes || {};

        // ── Route 行（极简） ──
        if (isRoute(span)) {
          return (
            <div key={span.id} id={`step-${span.id}`} className="flex items-center gap-4 py-1.5 px-3 text-[10px] text-slate-400">
              <span className="w-28 shrink-0 flex items-center gap-1">
                <span className="text-[9px]">🔀</span>
                <span className="text-slate-500">{span.name}</span>
              </span>
              <span className="flex-1">
                <span className="text-violet-600 bg-violet-50 rounded px-1.5 py-0.5 font-mono text-[9px]">
                  {attrs.condition || ""} → {attrs.result || attrs.edge || ""}
                </span>
              </span>
              <span className="w-24 shrink-0 text-right font-mono text-slate-400">{span.duration_ms}ms</span>
              <span className="w-56 shrink-0 hidden xl:block"></span>
              <span className="w-10 shrink-0"></span>
            </div>
          );
        }

        // ── Round 分组标题 ──
        if (isRound(span)) {
          const roundNum = attrs.round || "?";
          const dispatched = attrs.dispatched_skills || attrs.dispatched || "";
          const degraded = attrs.degraded_steps?.length > 0;
          const fallbackWarn = isFallback(span);

          return (
            <div key={span.id} id={`step-${span.id}`}>
              <div className={`flex items-center gap-4 py-2 px-3 rounded-md text-[11px] font-medium ${
                fallbackWarn ? "bg-amber-50 text-amber-700" : "bg-indigo-50 text-indigo-700"
              }`}>
                <span className="w-28 shrink-0 flex items-center gap-1">
                  {fallbackWarn ? "⚠️" : "🔄"} Round {roundNum}
                  {fallbackWarn && <span className="text-[9px] text-amber-600">降级</span>}
                </span>
                <span className="flex-1 text-[10px] text-slate-500 font-normal">
                  {degraded && <span className="text-amber-600">降级步骤: {attrs.degraded_steps?.join(", ")} · </span>}
                  {Array.isArray(dispatched) ? `dispatch: ${dispatched.join(", ")}` : dispatched ? `dispatch: ${dispatched}` : attrs.all_done ? "全部完成" : ""}
                  {attrs.degradation_reason && <span className="text-amber-600"> · {attrs.degradation_reason}</span>}
                </span>
                <span className="w-24 shrink-0 text-right font-mono text-xs">{span.duration_ms}ms</span>
                <span className="w-56 shrink-0 hidden xl:block"></span>
                <span className="w-10 shrink-0"></span>
              </div>
            </div>
          );
        }

        // ── 普通 Span 行 ──
        return (
          <div key={span.id} id={`step-${span.id}`}>
            <div
              onClick={() => onToggle?.(span.id)}
              style={{ paddingLeft: `${12 + depth * 16}px` }}
              className={`group flex items-center gap-4 py-2.5 px-3 rounded-md transition-colors cursor-pointer ${
                isHighlight
                  ? "bg-violet-100 ring-2 ring-violet-400"
                  : isRerankZero
                  ? "bg-red-50 border border-red-100"
                  : isSlowest
                  ? "bg-amber-50/50"
                  : "hover:bg-slate-50"
              }`}
            >
              {/* Label + indent indicator */}
              <div className="w-28 shrink-0 flex items-center gap-2">
                {depth > 0 && <span className="text-[9px] text-slate-300">{depth === 1 ? "└" : "├"}</span>}
                <span className={`inline-block w-1.5 h-1.5 rounded-full ${
                  span.status === "skipped" ? "bg-slate-300" : span.status === "error" ? "bg-red-400" : "bg-violet-500"
                }`} />
                <span className={`text-xs ${span.status === "skipped" ? "text-slate-400 line-through" : "text-slate-700"}`}>
                  {span.name}
                </span>
              </div>

              {/* Bar */}
              <div className="flex-1 h-6 bg-slate-100 rounded-sm overflow-hidden relative">
                <div className={`h-full rounded-sm transition-all duration-300 ${spanColor(span.status, span.duration_ms)}`}
                  style={{ width: `${Math.min(ratio, 100)}%` }} />
                {span.duration_ms > 0 && (
                  <span className={`absolute inset-y-0 flex items-center text-[10px] font-mono tabular-nums ${ratio > 25 ? "text-white pl-2 drop-shadow-sm" : "text-slate-500"}`}
                    style={{ left: ratio > 25 ? "0" : `calc(${Math.min(ratio, 100)}% + 6px)` }}>
                    {(span.duration_ratio * 100).toFixed(0)}%
                  </span>
                )}
              </div>

              {/* Duration */}
              <div className="w-24 shrink-0 text-right">
                <span className={`text-xs font-mono tabular-nums ${
                  isSlowest ? "text-red-500 font-bold" : span.status === "skipped" ? "text-slate-400" : "text-slate-600"
                }`}>{span.duration_ms}ms</span>
              </div>

              {/* Metrics */}
              <div className="w-56 shrink-0 hidden xl:block">
                <SpanMetrics span={span} />
              </div>

              {/* JSON toggle button */}
              <div className="w-10 shrink-0 flex justify-center">
                <button
                  onClick={(e) => { e.stopPropagation(); onJsonToggle?.(span.id); }}
                  title="查看 Raw JSON"
                  className={`text-[10px] px-1.5 py-0.5 rounded transition-colors ${
                    isJsonOpen ? "bg-slate-700 text-white" : "text-slate-300 hover:text-slate-600 hover:bg-slate-100"
                  }`}
                >
                  {"{ }"}
                </button>
              </div>
            </div>

            {/* Raw JSON 展开 */}
            {isJsonOpen && <SpanJsonPanel span={span} />}
          </div>
        );
      })}

      {/* Legend */}
      <div className="flex items-center gap-4 px-3 pt-3 text-[10px] text-slate-400">
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-violet-500" /> 正常</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-amber-500" /> &gt;1s</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-slate-200 border border-dashed border-slate-300" /> 跳过</span>
        <span className="flex items-center gap-1 ml-auto">{steps.length} span · {totalMs}ms</span>
      </div>
    </div>
  );
}
