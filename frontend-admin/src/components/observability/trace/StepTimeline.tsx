"use client";

import { useMemo, useState } from "react";
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
    // 顶层检索 span 用 total_docs（retrieved_chunks 为旧字段名），两者回退兼容
    const chunks = m.retrieved_chunks ?? m.total_docs;
    return <span className="text-[11px] text-slate-500">召回 {safeNum(chunks)} chunks</span>;
  }
  if (span.type === "rerank") {
    return <span className={`text-[11px] ${Number(m.output_docs ?? 1) === 0 ? "text-red-500 font-semibold" : "text-slate-500"}`}>输入 {safeNum(m.input_docs)} → 输出 {safeNum(m.output_docs)} (阈值 {safeNum(m.threshold)})</span>;
  }
  if (span.type === "llm_call") {
    // token_source=unavailable 表示采集链路失败，显示"未采集"而非误导性的 0
    if (m.token_source === "unavailable") {
      return <span className="text-[11px] text-amber-500" title="proxy ContextVar 与 response_metadata 均未返回 token">Token 未采集</span>;
    }
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
  // 大 trace 的 span JSON 序列化开销高，序列化结果随 span 缓存（重渲染不再重复 stringify）
  const json = useMemo(() => JSON.stringify(span, null, 2), [span]);

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

// -------- 判断是否应该自动展开（用于大耗时 span） --------

function shouldAutoExpand(span: Span): boolean {
  // 元数据生成的 7 个子阶段应该默认展开
  const metadataSpans = ['classify', 'quality', 'dedup_minhash', 'rule_extract', 'domain_classify', 'llm_generate', 'index_embed'];
  if (metadataSpans.some(id => span.id.includes(id) || span.name.toLowerCase().includes('元数据'))) {
    return true;
  }
  // 耗时 > 1s 的 span 建议展开
  if (span.duration_ms > 1000) {
    return true;
  }
  return false;
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
  autoExpandLarge?: boolean;  // 是否自动展开大耗时 span
}

export default function StepTimeline({ steps, totalMs, onToggle, expanded, jsonExpanded, onJsonToggle, highlightStepId, autoExpandLarge }: Props) {
  const maxMs = Math.max(...steps.map((s) => s.duration_ms), 1);

  // 判断 kind 是否属于 LangGraph 特殊节点
  const isRound = (s: Span) => (s as any).kind === "graph_loop" || (s as any).kind === "graph_fallback";
  const isRoute = (s: Span) => (s as any).kind === "graph_route";
  const isFallback = (s: Span) => (s as any).kind === "graph_fallback";

  // ── 构建树结构并按 DFS 序排列 ──
  const spanById = new Map<string, Span>();
  for (const s of steps) spanById.set(s.id, s);

  const childrenOf = new Map<string, Span[]>();
  const hasParent = new Set<string>();
  for (const s of steps) {
    const pid = s.parent_id;
    if (pid && spanById.has(pid)) {
      hasParent.add(s.id);
      if (!childrenOf.has(pid)) childrenOf.set(pid, []);
      childrenOf.get(pid)!.push(s);
    }
  }
  for (const [, kids] of childrenOf) kids.sort((a, b) => a.start_time.localeCompare(b.start_time));

  const roots = steps.filter((s) => !hasParent.has(s.id));
  roots.sort((a, b) => a.start_time.localeCompare(b.start_time));

  const ordered: { span: Span; depth: number; isLast: boolean }[] = [];

  function dfs(node: Span, depth: number, isLast: boolean) {
    ordered.push({ span: node, depth, isLast });
    const kids = childrenOf.get(node.id) || [];
    kids.forEach((kid, i) => dfs(kid, depth + 1, i === kids.length - 1));
  }

  roots.forEach((r, i) => dfs(r, 0, i === roots.length - 1));

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

      {ordered.map(({ span, depth, isLast }) => {
        const ratio = Math.max(span.duration_ratio * 100, span.status === "skipped" ? 0 : 0.3);
        const isSlowest = span.duration_ms === maxMs && span.duration_ms > 0;
        const isRerankZero = span.type === "rerank" && Number(span.metrics?.output_docs ?? -1) === 0;
        const isHighlight = highlightStepId === span.id;
        const isJsonOpen = jsonExpanded?.has(span.id);
        const attrs = (span as any).attributes || {};
        
        // 判断是否应该展开（用户手动 + 自动）
        const shouldExpand = expanded?.has(span.id) || (autoExpandLarge && shouldAutoExpand(span));

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
        const retryEvents = (span.events || []).filter((ev) =>
          String((ev as any)?.name || "").startsWith("retry_")
        );
        return (
          <div key={span.id} id={`step-${span.id}`}>
            <div
              onClick={() => onToggle?.(span.id)}
              style={{ paddingLeft: `${12 + depth * 16}px` }}
              className={`group flex items-center gap-4 py-2.5 px-3 rounded-md transition-colors cursor-pointer ${
                isHighlight
                  ? "bg-violet-100 ring-2 ring-violet-400"
                  : span.status === "error"
                  ? "bg-red-50 border border-red-200"
                  : isRerankZero
                  ? "bg-red-50 border border-red-100"
                  : isSlowest
                  ? "bg-amber-50/50"
                  : "hover:bg-slate-50"
              }`}
            >
              {/* Label + indent indicator */}
              <div className="w-28 shrink-0 flex items-center gap-2">
                {depth > 0 && <span className="text-[9px] text-slate-300">{isLast ? "└" : "├"}</span>}
                <span className={`inline-block w-1.5 h-1.5 rounded-full ${
                  span.status === "skipped" ? "bg-slate-300" : span.status === "error" ? "bg-red-400" : "bg-violet-500"
                }`} />
                <span className={`text-xs ${span.status === "skipped" ? "text-slate-400 line-through" : "text-slate-700"}`}>
                  {span.name}
                </span>
                {retryEvents.length > 0 && (
                  <span
                    className="inline-flex items-center px-1 py-0.5 rounded bg-orange-100 text-orange-700 text-[9px] font-semibold"
                    title={`执行了 ${retryEvents.length + 1} 次（重试 ${retryEvents.length} 次）`}
                  >
                    ↻{retryEvents.length}
                  </span>
                )}
                {span.status === "error" && (
                  <span className="inline-flex items-center px-1 py-0.5 rounded bg-red-100 text-red-700 text-[9px] font-semibold">失败</span>
                )}
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

            {/* 重试链：BaseSkill 每次 attempt 记一条 retry_N 事件 */}
            {isJsonOpen && retryEvents.length > 0 && (
              <div className="mt-1 ml-8 bg-orange-50 border border-orange-100 rounded-md p-2.5 space-y-1.5">
                <p className="text-[10px] uppercase tracking-wider text-orange-600 font-semibold">
                  🔁 重试链（共 {retryEvents.length + 1} 次尝试）
                </p>
                {retryEvents.map((ev, i) => (
                  <div key={i} className="flex items-start gap-2 text-[10px]">
                    <span className={`px-1 rounded font-mono font-semibold ${
                      (ev as any)?.level === "error" ? "bg-red-100 text-red-700" : "bg-orange-100 text-orange-700"
                    }`}>
                      {String((ev as any)?.name || `retry_${i + 1}`)}
                    </span>
                    <span className="text-slate-600">{String((ev as any)?.message || "")}</span>
                    <span className="ml-auto text-slate-400 font-mono shrink-0">
                      {String((ev as any)?.timestamp || "").slice(11, 19)}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {/* Raw JSON 展开 */}
            {isJsonOpen && <SpanJsonPanel span={span} />}
          </div>
        );
      })}

      {/* Legend */}
      <div className="flex items-center gap-4 px-3 pt-3 text-[10px] text-slate-400">
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-violet-500" /> 正常</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-amber-500" /> &gt;1s</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-red-400" /> 失败</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-slate-200 border border-dashed border-slate-300" /> 跳过</span>
        <span className="flex items-center gap-1">↻N = 重试 N 次</span>
        <span className="flex items-center gap-1 ml-auto">{steps.length} span · {totalMs}ms</span>
      </div>
    </div>
  );
}
