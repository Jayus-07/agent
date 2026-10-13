"use client";

import { useMemo, useState } from "react";
import { Span, spanColor, SPAN_TYPE_LABELS, safeNum } from "@/types/trace";
import { safeDiagnosticCode, safeRuntimeLabel } from "@/lib/observability/runtimeStatus";

// 注：safeNum 已统一在 @/types/trace.ts 导出，避免重复实现。

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
    if (id === "mq_check" || span.name === "MultiQuery") return <span className="text-[11px] text-slate-500">触发:{String(m.triggered ?? false)} 模式:{safeRuntimeLabel(m.mode, "未采集")}</span>;
    if (id === "citation" || span.name === "Citation") return <span className="text-[11px] text-slate-500">验证:{safeNum(m.verified_citations)}/{safeNum(m.total_citations)}</span>;
  }
  const typeLabel = SPAN_TYPE_LABELS[span.type] || span.type;
  return <span className="text-[11px] text-slate-400">{typeLabel}</span>;
}

function spanStatusLabel(span: Span): string {
  const status = String(span.status || "").toLowerCase();
  if (status === "failed") return "业务失败";
  if (status === "degraded") return "降级完成";
  if (status === "timeout") return "超时";
  if (status === "cancelled" || status === "canceled") return "已取消";
  if (status === "partial") return "部分成功";
  if (status === "rejected") return "已拒绝";
  if (status === "error") return "错误";
  if (status === "skipped") return "已跳过";
  if (status === "running") return "运行中";
  if (status === "success") return "成功";
  return "状态未知";
}

function SpanDetails({ span }: { span: Span }) {
  const output = span.output;
  const variants = Array.isArray(output?.variants) ? output.variants : [];
  const queryResults = Array.isArray(output?.query_results) ? output.query_results : [];
  const events = span.events || [];
  const errorCode = [span.attributes?.error_code, span.metrics.error_code, output?.error_code]
    .map(safeDiagnosticCode)
    .find((value) => value !== null) ?? null;

  return (
    <div className="mt-1 ml-8 rounded-md border border-slate-200 bg-white p-3 space-y-3 text-[11px]">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span><span className="text-slate-400">步骤状态：</span><b className={
          spanStatusLabel(span) === "成功" ? "text-emerald-600" : spanStatusLabel(span) === "错误" || spanStatusLabel(span) === "超时" ? "text-red-700" : "text-amber-700"
        }>{spanStatusLabel(span)}</b></span>
        <span><span className="text-slate-400">类型：</span><span className="text-slate-700">{SPAN_TYPE_LABELS[span.type] || span.type}</span></span>
        <span><span className="text-slate-400">Span：</span><span className="font-mono text-slate-600">{safeRuntimeLabel(span.id, "执行步骤")}</span></span>
      </div>

      {(errorCode || span.errors.length > 0 || events.some((event) => event.level === "error")) && (
        <div className="rounded border border-red-100 bg-red-50 px-2.5 py-2 text-red-700">
          <p className="font-semibold">诊断信息</p>
          <p>{errorCode ? `错误码：${errorCode}` : "执行包含错误事件；错误文本已隐藏。"}</p>
        </div>
      )}

      {(span.id.includes("query_rewrite") || span.name.toLowerCase().includes("改写")) && (
        <section>
          <h4 className="font-semibold text-slate-600 mb-1">多查询改写 · {variants.length} 条</h4>
          <p className="text-slate-500">查询文本已隐藏。</p>
        </section>
      )}

      {queryResults.length > 0 && (
        <section>
          <h4 className="font-semibold text-slate-600 mb-1">各查询检索结果 · {queryResults.length} 条</h4>
          <p className="text-slate-500">查询文本和逐项结果已隐藏。</p>
        </section>
      )}

      {events.length > 0 && (
        <section>
          <h4 className="font-semibold text-slate-600 mb-1">执行事件 · {events.length} 条</h4>
          <p className="text-slate-500">
            {events.filter((event) => event.level === "error").length > 0 && `${events.filter((event) => event.level === "error").length} 条错误级 · `}
            {events.filter((event) => event.level === "warn").length > 0 && `${events.filter((event) => event.level === "warn").length} 条警告级 · `}
            事件标识与消息正文已隐藏。
          </p>
        </section>
      )}

      <p className="text-slate-500">Span 输入和输出参数已隐藏。</p>
    </div>
  );
}

// -------- main --------

interface Props {
  steps: Span[];
  totalMs: number;
  onToggle?: (id: string) => void;
  expanded?: Set<string>;
  highlightStepId?: string | null;
}

export default function StepTimeline({ steps, totalMs, onToggle, expanded, highlightStepId }: Props) {
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
              <button type="button" onClick={expandAll} className="min-h-10 rounded px-1 py-0.5 text-violet-500 hover:bg-violet-50 hover:text-violet-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent" title="展开全部子步骤">展开</button>
              <span className="text-slate-300">/</span>
              <button type="button" onClick={collapseAll} className="min-h-10 rounded px-1 py-0.5 text-violet-500 hover:bg-violet-50 hover:text-violet-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent" title="收起全部子步骤">收起</button>
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
        const attrs = (span as any).attributes || {};
        const stepLabel = safeRuntimeLabel(span.name, "执行步骤");
        const sourceTraceId = safeRuntimeLabel(attrs.source_trace_id, "");
        
        // 所有 Span 默认折叠，只有用户点击该行后才显示明细。
        const shouldExpand = expanded?.has(span.id) || false;
        const rowStatusLabel = spanStatusLabel(span);
        const statusDotColor = rowStatusLabel === "成功" ? "bg-emerald-500"
          : rowStatusLabel === "超时" || rowStatusLabel === "运行中" || rowStatusLabel === "部分成功" || rowStatusLabel === "降级完成" ? "bg-amber-500"
          : rowStatusLabel === "错误" || rowStatusLabel === "业务失败" || rowStatusLabel === "已拒绝" ? "bg-red-400"
          : rowStatusLabel === "已跳过" || rowStatusLabel === "已取消" || rowStatusLabel === "状态未知" ? "bg-slate-300"
          : "bg-violet-500";
        const statusBadgeClass = rowStatusLabel === "超时" || rowStatusLabel === "运行中" || rowStatusLabel === "部分成功" || rowStatusLabel === "降级完成"
          ? "bg-amber-100 text-amber-700"
          : rowStatusLabel === "错误" || rowStatusLabel === "业务失败" || rowStatusLabel === "已拒绝"
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
                <span className="text-slate-500">{stepLabel}</span>
              </span>
              <span className="flex-1">
                <span className="text-violet-600 bg-violet-50 rounded px-1.5 py-0.5 font-mono text-[9px]">
                  已执行分支 · 条件值已隐藏
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
          const roundNum = typeof attrs.round === "number" && Number.isFinite(attrs.round) ? attrs.round : "?";
          const dispatched = attrs.dispatched_skills ?? attrs.dispatched;
          const dispatchCount = Array.isArray(dispatched) ? dispatched.length : dispatched ? 1 : 0;
          const degradedCount = Array.isArray(attrs.degraded_steps) ? attrs.degraded_steps.length : 0;
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
                  {degradedCount > 0 && <span className="text-amber-600">降级步骤 {degradedCount} 项 · </span>}
                  {dispatchCount > 0 ? `派发 ${dispatchCount} 项` : attrs.all_done === true ? "全部完成" : ""}
                  {attrs.degradation_reason && <span className="text-amber-600"> · 降级原因文本已隐藏</span>}
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
              style={{ paddingLeft: `${12 + depth * 16}px` }}
              className={`group flex items-center gap-4 py-2.5 px-3 rounded-md transition-colors ${
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
                    type="button"
                    aria-label={effectiveCollapsed.has(span.id) ? `展开 ${stepLabel} 子步骤` : `收起 ${stepLabel} 子步骤`}
                    onClick={(e) => { e.stopPropagation(); toggleCollapse(span.id); }}
                    title={effectiveCollapsed.has(span.id) ? "展开子步骤" : "收起子步骤"}
                    className="min-h-10 w-8 shrink-0 text-[9px] text-slate-400 hover:text-violet-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                  >
                    {effectiveCollapsed.has(span.id) ? "▶" : "▼"}
                  </button>
                ) : (
                  <span className="w-3 shrink-0" />
                )}
                <span className={`inline-block w-1.5 h-1.5 rounded-full ${statusDotColor}`} />
                <button
                  type="button"
                  onClick={() => onToggle?.(span.id)}
                  aria-expanded={shouldExpand}
                  aria-controls={`span-details-${span.id}`}
                  className={`min-h-10 rounded text-left text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 ${span.status === "skipped" ? "text-slate-400 line-through" : "text-slate-700"}`}
                >
                  {stepLabel}
                </button>
                {sourceTraceId && (
                  <span
                    className="inline-flex items-center rounded bg-indigo-50 px-1 py-0.5 text-[9px] text-indigo-600"
                    title={`来自子 Trace ${sourceTraceId}`}
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

              <div className="w-10 shrink-0" />
            </div>

            {shouldExpand && <div id={`span-details-${span.id}`}><SpanDetails span={span} /></div>}

            {/* 重试事件只显示次数与时间，不显示事件标识或消息正文。 */}
            {shouldExpand && retryEvents.length > 0 && (
              <div className="mt-1 ml-8 bg-orange-50 border border-orange-100 rounded-md p-2.5 space-y-1.5">
                <p className="text-[10px] uppercase tracking-wider text-orange-600 font-semibold">
                  🔁 重试链（共 {retryEvents.length + 1} 次尝试）
                </p>
                {retryEvents.map((ev, i) => (
                  <div key={i} className="flex items-start gap-2 text-[10px]">
                    <span className={`px-1 rounded font-mono font-semibold ${
                      (ev as any)?.level === "error" ? "bg-red-100 text-red-700" : "bg-orange-100 text-orange-700"
                    }`}>
                      重试 {i + 1}
                    </span>
                    <span className="ml-auto text-slate-400 font-mono shrink-0">
                      {String((ev as any)?.timestamp || "").slice(11, 19)}
                    </span>
                  </div>
                ))}
              </div>
            )}

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
