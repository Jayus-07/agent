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
    const firstValue = m[firstKey];
    return <span className="text-[11px] text-slate-400">{typeLabel} · {firstKey}: {typeof firstValue === "number" ? safeNum(firstValue) : String(firstValue)}</span>;
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

function stringifyDetail(value: unknown): string {
  if (value === undefined || value === null) return "未记录";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function spanStatusLabel(span: Span): string {
  const status = String(span.status || "").toLowerCase();
  const attrs = span.attributes || {};
  const metrics = span.metrics || {};
  const errorType = String(attrs.error_type || attrs.error_class || metrics.error_type || "").toLowerCase();
  const failureText = [
    ...((span.errors || []).map(String)),
    ...(span.events || []).map((event) => `${event.name || ""} ${event.message || ""}`),
    typeof metrics.error === "string" ? metrics.error : "",
    typeof metrics.error_message === "string" ? metrics.error_message : "",
  ].join(" ").toLowerCase();

  if (status === "timeout" || errorType.includes("timeout") || /timeout|超时/.test(failureText)) {
    return "超时";
  }
  if (status === "cancelled" || status === "canceled") return "已取消";
  if (status === "partial") return "部分成功";
  if (status === "rejected") return "证据门拒绝";
  if (status === "error" || (span.events || []).some((event) => event.level === "error")) {
    return "异常";
  }
  if (status === "skipped") return "已跳过";
  if (status === "running") return "执行中";
  if (status === "success") return "成功";
  return status || "未记录";
}

function spanDiagnostics(span: Span): string[] {
  const attrs = span.attributes || {};
  const output = span.output || {};
  const metrics = span.metrics || {};
  const diagnostics = [
    ...(span.errors || []),
    ...((span.events || [])
      .filter((event) => event.level === "error")
      .map((event) => event.message || event.name)),
    typeof attrs.error === "string" ? attrs.error : "",
    typeof attrs.error_message === "string" ? attrs.error_message : "",
    typeof metrics.error === "string" ? metrics.error : "",
    typeof metrics.error_message === "string" ? metrics.error_message : "",
    typeof output.error === "string" ? output.error : "",
  ];
  return [...new Set(diagnostics.map((item) => String(item || "").trim()).filter(Boolean))];
}

function queryResultStatus(status: unknown): string {
  const value = String(status || "").toLowerCase();
  if (value === "success" || value === "done") return "成功";
  if (value === "partial") return "部分成功";
  if (value === "timeout") return "超时";
  if (value === "error" || value === "failed") return "失败";
  if (value === "cancelled" || value === "canceled") return "已取消";
  return value || "未记录";
}

function SpanDetails({ span }: { span: Span }) {
  const attrs = span.attributes || {};
  const input = span.input;
  const output = span.output;
  const capabilityName = [
    attrs.capability,
    input?.capability,
  ].find((value) => typeof value === "string" && value.trim());
  const toolName = [
    attrs.tool_name,
    attrs.tool_id,
    attrs.skill_id,
    input?.tool_name,
    input?.tool_id,
    input?.tool,
  ].find((value) => typeof value === "string" && value.trim());
  const variants = Array.isArray(output?.variants) ? output.variants : [];
  const queryResults = Array.isArray(output?.query_results) ? output.query_results : [];
  const events = span.events || [];
  const isMultiQueryRewrite = span.id.includes("query_rewrite")
    || span.name.toLowerCase().includes("改写");
  const diagnostics = spanDiagnostics(span);
  const outputRemainder = output
    ? Object.fromEntries(Object.entries(output).filter(([key]) =>
      !(isMultiQueryRewrite && key === "variants") && key !== "query_results"))
    : undefined;

  return (
    <div className="mt-1 ml-8 rounded-md border border-slate-200 bg-white p-3 space-y-3 text-[11px]">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span><span className="text-slate-400">步骤状态：</span><b className={
          spanStatusLabel(span) === "成功" ? "text-emerald-600" : "text-amber-700"
        }>{spanStatusLabel(span)}</b></span>
        <span><span className="text-slate-400">类型：</span><span className="text-slate-700">{SPAN_TYPE_LABELS[span.type] || span.type}</span></span>
        <span><span className="text-slate-400">Span：</span><span className="font-mono text-slate-600">{span.id}</span></span>
        {typeof capabilityName === "string" && capabilityName.trim() && <span><span className="text-slate-400">调用能力：</span><span className="font-mono text-slate-700">{capabilityName}</span></span>}
        {typeof toolName === "string" && toolName.trim() && <span><span className="text-slate-400">实际 Tool：</span><span className="font-mono text-slate-700">{toolName}</span></span>}
      </div>

      {diagnostics.length > 0 && (
        <div className="rounded border border-red-100 bg-red-50 px-2.5 py-2 text-red-700">
          <p className="font-semibold mb-1">失败 / 超时原因</p>
          {diagnostics.map((message, index) => <p key={`${index}-${message}`} className="break-words">{message}</p>)}
        </div>
      )}

      {isMultiQueryRewrite && (
        <section>
          <h4 className="font-semibold text-slate-600 mb-1">多查询改写 · {variants.length} 条</h4>
          {variants.length > 0 ? (
            <ol className="list-decimal pl-5 space-y-1 text-slate-700">
              {variants.map((query, index) => <li key={`${index}-${String(query)}`} className="break-words">{String(query)}</li>)}
            </ol>
          ) : <p className="text-slate-400 italic">未记录改写结果（可能未触发或改写失败）</p>}
        </section>
      )}

      {queryResults.length > 0 && (
        <section>
          <h4 className="font-semibold text-slate-600 mb-1">各查询检索结果 · {queryResults.length} 条</h4>
          <div className="space-y-1.5">
            {queryResults.map((result, index) => {
              const item = result && typeof result === "object" ? result as Record<string, unknown> : {};
              const statusLabel = queryResultStatus(item.status);
              return (
                <div key={`${index}-${String(item.query || "query")}`} className="rounded border border-slate-100 bg-slate-50 px-2.5 py-2">
                  <div className="flex items-start gap-2">
                    <span className="shrink-0 font-mono text-slate-400">Q{index + 1}</span>
                    <span className="min-w-0 flex-1 break-words text-slate-700">{String(item.query || "查询内容未记录")}</span>
                    <span className={statusLabel === "成功" ? "shrink-0 text-emerald-600" : "shrink-0 text-red-600"}>{statusLabel}</span>
                  </div>
                  <div className="mt-1 pl-7 text-[10px] text-slate-500">
                    召回 {item.retrieved_docs === undefined ? "未记录" : String(item.retrieved_docs)}
                    {item.retained_docs !== undefined && ` · 去重保留 ${String(item.retained_docs)}`}
                    {item.duration_ms !== undefined && ` · ${String(item.duration_ms)}ms`}
                  </div>
                  {item.error !== undefined && item.error !== null && item.error !== "" && <p className="mt-1 pl-7 break-words text-red-600">{String(item.error)}</p>}
                </div>
              );
            })}
          </div>
        </section>
      )}

      {events.length > 0 && (
        <section>
          <h4 className="font-semibold text-slate-600 mb-1">执行事件 · {events.length} 条</h4>
          <div className="space-y-2">
            {events.map((event, index) => {
              const eventData = (event.attributes || event.data || {}) as Record<string, unknown>;
              const chunks = Array.isArray(eventData.chunks) ? eventData.chunks : [];
              const remainingData = Object.fromEntries(
                Object.entries(eventData).filter(([key]) => key !== "chunks"),
              );
              return (
                <div key={`${event.name}-${index}`} className="rounded border border-slate-100 bg-slate-50 px-2.5 py-2">
                  <div className="flex items-start gap-2">
                    <span className={`shrink-0 font-mono ${event.level === "error" ? "text-red-600" : event.level === "warn" ? "text-amber-600" : "text-slate-500"}`}>
                      {event.name}
                    </span>
                    {event.message && <span className="min-w-0 flex-1 break-words text-slate-600">{event.message}</span>}
                  </div>
                  {chunks.length > 0 && (
                    <div className="mt-2 space-y-1.5">
                      {chunks.map((chunk, chunkIndex) => {
                        const item = chunk && typeof chunk === "object" ? chunk as Record<string, unknown> : {};
                        return (
                          <div key={`${chunkIndex}-${String(item.chunk_id || "chunk")}`} className="rounded bg-white px-2 py-1.5">
                            <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-slate-500">
                              <span>证据 {String(item.chunk_id || `#${chunkIndex + 1}`)}</span>
                              {typeof item.source === "string" && item.source && <span>文档：{item.source}</span>}
                              {typeof item.source_query === "string" && item.source_query && <span>来源查询：{item.source_query}</span>}
                            </div>
                            {typeof item.snippet === "string" && item.snippet && <p className="mt-1 break-words text-slate-700">{item.snippet}</p>}
                          </div>
                        );
                      })}
                    </div>
                  )}
                  {Object.keys(remainingData).length > 0 && (
                    <pre className="mt-1 max-h-36 overflow-auto whitespace-pre-wrap break-words font-mono text-[9px] text-slate-500">{stringifyDetail(remainingData)}</pre>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <section className="min-w-0">
          <h4 className="font-semibold text-slate-600 mb-1">输入参数</h4>
          <pre className="max-h-48 overflow-auto rounded bg-slate-50 p-2 font-mono text-[10px] leading-relaxed whitespace-pre-wrap break-words text-slate-600">{stringifyDetail(input)}</pre>
        </section>
        <section className="min-w-0">
          <h4 className="font-semibold text-slate-600 mb-1">输出结果</h4>
          <pre className="max-h-48 overflow-auto rounded bg-slate-50 p-2 font-mono text-[10px] leading-relaxed whitespace-pre-wrap break-words text-slate-600">{stringifyDetail(outputRemainder)}</pre>
        </section>
      </div>
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

  // 折叠状态：默认展开根节点、收起其下的嵌套层级。
  // 旧实现把 30+ 个 span 连同嵌套时间全部平铺，同一条耗时链在时间线和
  // 甘特图里被重复描画，用户无法分辨聚合行与真实步骤。
  // 注意不能"收起所有含子节点的 span"——那会把整棵树压成 1 行（root），
  // 用户看不到顶层流程；只收起 depth>=1 的子树，默认呈现顶层骨架。
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [collapseTouched, setCollapseTouched] = useState(false);
  const defaultCollapsed = useMemo(() => {
    const ids = new Set<string>();
    for (const r of roots) for (const kid of childrenOf.get(r.id) || []) ids.add(kid.id);
    return ids;
  }, [steps]);
  // 首次渲染后按默认规则收起；用户一旦手动操作即尊重其选择。
  const effectiveCollapsed = collapseTouched ? collapsed : defaultCollapsed;

  const collapsibleIds = useMemo(() => new Set(childrenOf.keys()), [childrenOf]);

  const toggleCollapse = (spanId: string) => {
    const base = collapseTouched ? collapsed : new Set(defaultCollapsed);
    const next = new Set(base);
    next.has(spanId) ? next.delete(spanId) : next.add(spanId);
    setCollapsed(next);
    setCollapseTouched(true);
  };

  const expandAll = () => { setCollapsed(new Set()); setCollapseTouched(true); };
  const collapseAll = () => { setCollapsed(new Set(collapsibleIds)); setCollapseTouched(true); };

  const ordered: { span: Span; depth: number; isLast: boolean; hasChildren: boolean }[] = [];

  function dfs(node: Span, depth: number, isLast: boolean) {
    const kids = childrenOf.get(node.id) || [];
    ordered.push({ span: node, depth, isLast, hasChildren: kids.length > 0 });
    if (effectiveCollapsed.has(node.id)) return;
    kids.forEach((kid, i) => dfs(kid, depth + 1, i === kids.length - 1));
  }

  roots.forEach((r, i) => dfs(r, 0, i === roots.length - 1));

  const hiddenCount = steps.length - ordered.length;

  return (
    <div className="space-y-1">
      {/* Header */}
      <div className="flex items-center gap-4 px-3 pb-2 mb-2 border-b border-slate-100 text-[10px] text-slate-400 uppercase tracking-wider">
        <span className="w-28 shrink-0 flex items-center gap-2">
          <span>步骤</span>
          {collapsibleIds.size > 0 && (
            <span className="flex items-center gap-1 normal-case tracking-normal">
              <button onClick={expandAll} className="rounded px-1 py-0.5 text-violet-500 hover:bg-violet-50 hover:text-violet-700" title="展开全部子步骤">展开</button>
              <span className="text-slate-300">/</span>
              <button onClick={collapseAll} className="rounded px-1 py-0.5 text-violet-500 hover:bg-violet-50 hover:text-violet-700" title="收起全部子步骤">收起</button>
            </span>
          )}
        </span>
        <span className="flex-1">耗时分布</span>
        <span className="w-24 shrink-0 text-right">耗时</span>
        <span className="w-56 shrink-0 hidden xl:block">关键指标</span>
        <span className="w-10 shrink-0"></span>
      </div>
      {hiddenCount > 0 && (
        <p className="px-3 pb-2 text-[10px] text-slate-400">
          已收起 {hiddenCount} 个嵌套子步骤，点击左侧三角展开
        </p>
      )}

      {ordered.map(({ span, depth, isLast, hasChildren }) => {
        const ratio = Math.max(span.duration_ratio * 100, span.status === "skipped" ? 0 : 0.3);
        const isSlowest = span.duration_ms === maxMs && span.duration_ms > 0;
        const isRerankZero = span.type === "rerank" && Number(span.metrics?.output_docs ?? -1) === 0;
        const isHighlight = highlightStepId === span.id;
        const isJsonOpen = jsonExpanded?.has(span.id);
        const attrs = (span as any).attributes || {};
        
        // 所有 Span 默认折叠，只有用户点击该行后才显示明细。
        const shouldExpand = expanded?.has(span.id) || false;
        const rowStatusLabel = spanStatusLabel(span);
        const statusDotColor = rowStatusLabel === "成功" ? "bg-emerald-500"
          : rowStatusLabel === "超时" || rowStatusLabel === "执行中" || rowStatusLabel === "部分成功" ? "bg-amber-500"
          : rowStatusLabel === "异常" || rowStatusLabel === "证据门拒绝" ? "bg-red-400"
          : rowStatusLabel === "已跳过" || rowStatusLabel === "已取消" ? "bg-slate-300"
          : "bg-violet-500";
        const statusBadgeClass = rowStatusLabel === "超时" || rowStatusLabel === "执行中" || rowStatusLabel === "部分成功"
          ? "bg-amber-100 text-amber-700"
          : rowStatusLabel === "异常" || rowStatusLabel === "证据门拒绝"
            ? "bg-red-100 text-red-700"
            : "bg-slate-100 text-slate-500";
        const barStatus = rowStatusLabel === "超时" ? "timeout"
          : rowStatusLabel === "部分成功" ? "partial"
          : span.status;

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
                {/* 折叠三角：只有含子步骤的行可点，避免与行点击展开明细冲突 */}
                {hasChildren ? (
                  <button
                    onClick={(e) => { e.stopPropagation(); toggleCollapse(span.id); }}
                    title={effectiveCollapsed.has(span.id) ? "展开子步骤" : "收起子步骤"}
                    className="w-3 shrink-0 text-[9px] text-slate-400 hover:text-violet-600"
                  >
                    {effectiveCollapsed.has(span.id) ? "▶" : "▼"}
                  </button>
                ) : (
                  <span className="w-3 shrink-0" />
                )}
                <span className={`inline-block w-1.5 h-1.5 rounded-full ${statusDotColor}`} />
                <span className={`text-xs ${span.status === "skipped" ? "text-slate-400 line-through" : "text-slate-700"}`}>
                  {span.name}
                </span>
                {typeof attrs.source_trace_id === "string" && (
                  <span
                    className="inline-flex items-center rounded bg-indigo-50 px-1 py-0.5 text-[9px] text-indigo-600"
                    title={`来自子 Trace ${attrs.source_trace_id}`}
                  >
                    子 Trace
                  </span>
                )}
                {retryEvents.length > 0 && (
                  <span
                    className="inline-flex items-center px-1 py-0.5 rounded bg-orange-100 text-orange-700 text-[9px] font-semibold"
                    title={`执行了 ${retryEvents.length + 1} 次（重试 ${retryEvents.length} 次）`}
                  >
                    ↻{retryEvents.length}
                  </span>
                )}
                {rowStatusLabel !== "成功" && rowStatusLabel !== "未记录" && (
                  <span className={`inline-flex items-center px-1 py-0.5 rounded text-[9px] font-semibold ${statusBadgeClass}`}>
                    {rowStatusLabel}
                  </span>
                )}
              </div>

              {/* Bar */}
              <div className="flex-1 h-6 bg-slate-100 rounded-sm overflow-hidden relative">
                <div className={`h-full rounded-sm transition-all duration-300 ${spanColor(barStatus, span.duration_ms)}`}
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

            {shouldExpand && <SpanDetails span={span} />}

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
