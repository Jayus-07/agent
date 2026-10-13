"use client";

import { useState, useMemo, useRef, useEffect } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { getTraceDetail } from "@/api/observability";
import dynamic from "next/dynamic";
import TraceOverviewCard from "@/components/observability/trace/TraceOverviewCard";
import InputOutputPanel from "@/components/observability/trace/InputOutputPanel";
import TraceBreadcrumb from "@/components/observability/trace/TraceBreadcrumb";
import TraceOutcomePanel from "@/components/observability/trace/TraceOutcomePanel";
import PromptVersionsBadge from "@/components/observability/trace/PromptVersionsBadge";
import LLMCallDetail from "@/components/observability/trace/LLMCallDetail";
import CostPanel from "@/components/observability/trace/CostPanel";
import HttpBreakdown from "@/components/observability/trace/HttpBreakdown";
import SpanTypeSummary from "@/components/observability/trace/SpanTypeSummary";
import { mergeChildTraceSpans } from "@/components/observability/trace/traceSpanTree";

// 两个可视化组件含 recharts，懒加载避免拖慢详情页首帧
const StepTimeline = dynamic(() => import("@/components/observability/trace/StepTimeline"), {
  ssr: false,
  loading: () => <div className="h-40 rounded-lg bg-slate-100 animate-pulse" />,
});
const FlameGraph = dynamic(() => import("@/components/observability/trace/FlameGraph"), {
  ssr: false,
  loading: () => <div className="h-40 rounded-lg bg-slate-100 animate-pulse" />,
});
import SpanTypeFilter from "@/components/observability/trace/SpanTypeFilter";
import GraphTopology, { hasGraphNodeExecution } from "@/components/observability/trace/GraphTopology";
import {
  formatTime,
  formatRelative,
  durationColor,
  TraceRecord,
  Span,
  safeNum,
} from "@/types/trace";
import { getRunStatus, getRunStatusLabel, getRunStatusTone, safeDiagnosticCode, safeRuntimeLabel } from "@/lib/observability/runtimeStatus";

// 注：safeNum 已统一在 @/types/trace.ts 导出。

/** 辅助：从 spans 中查找指定 id 的 span */
function findSpan(spans: Span[], id: string): Span | undefined {
  return spans.find((s) => s.id === id);
}

function traceReadFailure(error: unknown): { title: string; message: string } {
  const status = (error as { status?: number } | null)?.status;
  if (status === 401) return { title: "登录状态已失效", message: "请重新登录后再读取 Trace。" };
  if (status === 403) return { title: "当前身份无权读取", message: "该 Trace 受资源级权限保护。" };
  if (status === 429) return { title: "请求过于频繁", message: "请稍后重试读取 Trace。" };
  if (status && status >= 500) return { title: "Trace 服务暂不可用", message: "服务端读取失败，请稍后重试。" };
  return { title: "Trace 服务暂不可用", message: "读取详情失败，请检查连接后重试。" };
}

export default function TraceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());
  const [highlightStepId, setHighlightStepId] = useState<string | null>(null);
  const [activeSpanTypes, setActiveSpanTypes] = useState<Set<string>>(new Set());  // 空=全部

  // 异步加载：详情页需要单条 trace + 父子链
  const [trace, setTrace] = useState<TraceRecord | null>(null);
  const [parent, setParent] = useState<TraceRecord | null>(null);
  const [children, setChildren] = useState<TraceRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<{ title: string; message: string } | null>(null);
  const [relationshipLoadError, setRelationshipLoadError] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);

  const requestedReturnTo = searchParams.get("returnTo") || "";
  const returnTo = requestedReturnTo === "/observability/monitoring"
    || requestedReturnTo.startsWith("/observability/monitoring?")
    ? requestedReturnTo
    : "/observability/monitoring?tab=traces";
  const traceHref = (traceId: string) => `/observability/traces/${encodeURIComponent(traceId)}?${new URLSearchParams({ returnTo }).toString()}`;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setTrace(null);
        setLoadError(null);
    setRelationshipLoadError(false);
    (async () => {
      try {
        const t = await getTraceDetail(id);
        if (cancelled) return;
        setTrace(t);
        if (!t) {
          setParent(null);
          setChildren([]);
          setLoading(false);
          return;
        }
        // 关联 Trace 仍通过同一受资源授权的只读详情 API 获取。
        const relationError = () => { if (!cancelled) setRelationshipLoadError(true); };
        const pTask = t.parent_id
          ? getTraceDetail(t.parent_id).catch(() => { relationError(); return null; })
          : Promise.resolve(null);
        const descendants: TraceRecord[] = [];
        const seen = new Set<string>([t.id]);
        let pending = [...(t.children_ids || [])];
        while (pending.length > 0 && descendants.length < 100) {
          const batch = [...new Set(pending.filter((childId) => !seen.has(childId)))].slice(0, 25);
          if (batch.length === 0) break;
          const batchIds = new Set(batch);
          pending = pending.filter((childId) => !batchIds.has(childId));
          batch.forEach((childId) => seen.add(childId));
          const fetched = await Promise.all(batch.map((childId) => getTraceDetail(childId).catch(() => { relationError(); return null; })));
          for (const child of fetched) {
            if (!child) continue;
            descendants.push(child);
            pending.push(...(child.children_ids || []));
          }
        }
        const p = await pTask;
        if (cancelled) return;
        setParent(p);
        setChildren(descendants);
        setLoading(false);
      } catch (error) {
        if (cancelled) return;
        setTrace(null);
        setLoadError(traceReadFailure(error));
        setParent(null);
        setChildren([]);
        setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [id, loadAttempt]);

  const spans = (trace?.spans || []) as Span[];
  const graphNodes = trace?.graph?.nodes.filter((node) => hasGraphNodeExecution(node.id, spans)) || [];
  const graphNodeIds = new Set(graphNodes.map((node) => node.id));
  const graphEdges = trace?.graph?.edges.filter((edge) => graphNodeIds.has(edge.source) && graphNodeIds.has(edge.target)) || [];

  const timersRef = useRef<Set<ReturnType<typeof setTimeout>>>(new Set());
  useEffect(() => {
    return () => {
      timersRef.current.forEach(clearTimeout);
      timersRef.current.clear();
    };
  }, []);

  if (loading && !trace) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="text-center text-sm text-slate-500" role="status">正在读取 Trace 详情…</div>
      </div>
    );
  }

  if (!trace) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center px-4">
        <div className="max-w-md rounded-lg border border-slate-200 bg-white p-6 text-center">
          <h1 className="font-semibold text-slate-900">{loadError?.title || "无法查看此 Trace"}</h1>
          <p className="mt-2 text-sm text-slate-600">
            {loadError?.message || "记录不存在，或当前身份无权查看该受保护资源。"}
          </p>
          <div className="mt-4 flex justify-center gap-3">
            {loadError && <button type="button" onClick={() => setLoadAttempt((attempt) => attempt + 1)} className="min-h-10 rounded-md border border-slate-300 px-3 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600">重试</button>}
            <button type="button" onClick={() => router.push(returnTo)} className="min-h-10 rounded-md bg-blue-700 px-3 text-sm font-medium text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2">返回执行记录</button>
          </div>
        </div>
      </div>
    );
  }

  const hasError = Boolean(trace.error && Object.keys(trace.error).length > 0);
  const err = trace.error as Record<string, unknown>;
  const runStatus = getRunStatus(trace);

  // 业务 span 查找（按 type 或 id）
  const rerankSpan = spans.find((s) => s.type === "rerank" || s.id === "rerank");
  const rerankZero = rerankSpan && Number(rerankSpan.metrics?.output_docs ?? -1) === 0;
  const faithSpan = spans.find((s) => s.id === "faithfulness" || s.name === "Faithfulness");
  const mqSpan = spans.find((s) => s.id === "mq_check" || s.name === "MultiQuery");
  const errorStepId = safeRuntimeLabel(err.error_node, "");
  const errorCode = safeDiagnosticCode(err.code);
  const hasErrorCode = typeof err.code === "string" && err.code.trim().length > 0;

  // 主 Trace 与远端子 Trace 共用一条时间线；子 Trace Span 会映射到其工具调用下。
  const childSpans = mergeChildTraceSpans(spans, children);

  // Span Type 过滤
  const filteredSpans = activeSpanTypes.size === 0
    ? childSpans
    : childSpans.filter((s) => activeSpanTypes.has(s.type));

  const toggleStep = (spanId: string) => {
    const next = new Set(expandedSteps);
    next.has(spanId) ? next.delete(spanId) : next.add(spanId);
    setExpandedSteps(next);
  };

  const scrollToStep = (spanId: string) => {
    setHighlightStepId(spanId);
    timersRef.current.forEach(clearTimeout);
    timersRef.current.clear();

    const t1 = setTimeout(() => {
      const el = document.getElementById(`step-${spanId}`);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
      timersRef.current.delete(t1);
    }, 50);
    const t2 = setTimeout(() => {
      setHighlightStepId(null);
      timersRef.current.delete(t2);
    }, 2500);
    timersRef.current.add(t1);
    timersRef.current.add(t2);
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="max-w-[1440px] mx-auto px-4 py-5 sm:px-6 sm:py-6 space-y-5">
        {/* ── Breadcrumb ── */}
        <TraceBreadcrumb
          crumbs={[
            { label: "运行控制中心", href: returnTo },
            { label: "执行记录", href: returnTo },
            ...(parent ? [{ label: `父 · ${parent.id.slice(0, 8)}`, href: traceHref(parent.id) }] : []),
            { label: trace.id.slice(0, 12) },
          ]}
        />

        {/* ── Header ── */}
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-3 flex-wrap">
            <button type="button" onClick={() => router.push(returnTo)} className="min-h-10 rounded px-2 text-sm text-slate-600 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600">← 返回列表</button>
            <span className="font-mono text-slate-700 font-semibold">{trace.id.slice(0, 12)}</span>
            <span className={`inline-flex rounded border px-2 py-1 text-xs font-semibold ${getRunStatusTone(runStatus)}`}>{getRunStatusLabel(runStatus)}</span>
            {trace.workflow_name && (
              <span className="text-[10px] text-slate-400 bg-slate-100 rounded px-2 py-0.5 font-mono">
                    {safeRuntimeLabel(trace.workflow_name, "工作流")}
              </span>
            )}
            {trace.sla?.breached && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-red-100 text-red-700">
                ⚠️ SLA 违反 ({trace.sla.threshold_ms}ms)
              </span>
            )}
            <button type="button" onClick={() => void navigator.clipboard.writeText(trace.id)} className="min-h-10 rounded border border-slate-200 px-2 text-xs text-slate-600 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600">复制 ID</button>
            <span className="text-[10px] text-slate-400" title={formatTime(trace.timestamp)}>{formatRelative(trace.timestamp)}</span>
          </div>
        </div>

        {/* ── Parent / Children 关联 ── */}
        {(parent || children.length > 0) && (
          <div className="bg-violet-50/50 border border-violet-200 rounded-xl p-4">
            <p className="text-[10px] uppercase tracking-widest text-violet-700 mb-2">🔗 Trace 关联</p>
            <div className="space-y-1.5 text-xs">
              {parent && (
                <div className="flex items-center gap-2">
                  <span className="text-slate-500">父:</span>
                  <Link href={traceHref(parent.id)} className="font-mono text-violet-700 hover:underline">
                    {parent.id.slice(0, 16)}
                  </Link>
                  <span className={`rounded border px-2 py-0.5 text-[10px] ${getRunStatusTone(getRunStatus(parent))}`}>{getRunStatusLabel(getRunStatus(parent))}</span>
                </div>
              )}
              {children.length > 0 && (
                <div className="flex items-start gap-2">
                  <span className="text-slate-500 shrink-0">子 ({children.length}):</span>
                  <div className="flex flex-wrap gap-1.5">
                    {children.map((c) => (
                      <Link
                        key={c.id}
                        href={traceHref(c.id)}
                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-white border border-violet-200 hover:border-violet-400 transition-colors"
                      >
                        <span className="font-mono text-violet-700">{c.id.slice(0, 8)}</span>
                        <span className="text-slate-400">·</span>
                        <span className={`rounded border px-1.5 py-0.5 text-[10px] ${getRunStatusTone(getRunStatus(c))}`}>{getRunStatusLabel(getRunStatus(c))}</span>
                      </Link>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {relationshipLoadError && <p className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900" role="status">Trace 本身已读取；部分父子关联记录暂不可用。</p>}

        <div className="grid grid-cols-1 lg:grid-cols-4 gap-5">
          {/* 主区域 */}
          <div className="lg:col-span-3 space-y-5">
            {/* ── Overview ── */}
            <section>
              <h2 className="text-sm font-semibold text-slate-700 mb-3">一、业务结果</h2>
              <div className="space-y-3">
                <TraceOutcomePanel trace={trace} />
                <InputOutputPanel question={trace.question} answer={trace.answer_preview} error={trace.error} />
              </div>
            </section>

            <section>
              <h2 className="text-sm font-semibold text-slate-700 mb-3">执行摘要</h2>
              <TraceOverviewCard trace={trace} />
            </section>

            {/* ── LangGraph DAG（仅 Agent workflow） ── */}
            {trace.graph && graphNodes.length > 0 && (
              <section>
                <h2 className="text-sm font-semibold text-slate-700 mb-3">二、实际执行步骤 · LangGraph 拓扑</h2>
                <p className="mb-2 text-xs text-slate-500">仅显示可关联实际 Span 的节点；连线是后端编排拓扑关系，接口未提供边是否实际执行。</p>
                <GraphTopology
                  nodes={graphNodes}
                  edges={graphEdges}
                  spans={spans}
                  loopCount={trace.graph.loop_count}
                  maxLoops={trace.graph.max_loops}
                  degradationTriggered={trace.graph.degradation_triggered}
                  onNodeClick={scrollToStep}
                />
              </section>
            )}
            {trace.graph && graphNodes.length === 0 && (
              <p className="rounded-md border border-border-subtle bg-white px-4 py-3 text-sm text-text-secondary" role="status">后端返回了编排拓扑，但没有可关联的实际 Span；为避免把计划节点显示成已执行，本页已隐藏该拓扑。</p>
            )}

            {/* ── Span 时间线（耗时分布 + 类型过滤，合并自旧"耗时分布/时间线"两个重复区块） ── */}
            {childSpans.length > 0 && (
              <section>
                <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
                    <h2 className="text-sm font-semibold text-slate-700">二、实际执行步骤 · Span 时间线</h2>
                  <div className="flex items-center gap-4">
                    <span className="text-[10px] text-slate-400">
                      {filteredSpans.length}/{childSpans.length} span
                      {activeSpanTypes.size > 0 && <button onClick={() => setActiveSpanTypes(new Set())} className="ml-2 text-violet-500 hover:text-violet-700">清除过滤</button>}
                    </span>
                  </div>
                </div>
                <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-3">
                  <SpanTypeFilter spans={childSpans} activeTypes={activeSpanTypes} onChange={setActiveSpanTypes} />
                  <div className="border-t border-slate-100 pt-3">
                    <StepTimeline
                      steps={filteredSpans}
                      totalMs={trace.duration_ms}
                      onToggle={toggleStep}
                      expanded={expandedSteps}
                      highlightStepId={highlightStepId}
                    />
                  </div>

                  {/* 展开后追加 HTTP 拆分 */}
                  {Array.from(expandedSteps).map((sid) => {
                    const span = findSpan(spans, sid);
                    if (!span || !span.http_breakdown) return null;
                    return (
                      <div key={`http-${sid}`} className="border-t border-slate-100 pt-3">
                        <p className="text-[10px] uppercase tracking-wider text-slate-400 mb-2">📡 {safeRuntimeLabel(span.name, "执行步骤")} · HTTP 耗时拆分</p>
                        <HttpBreakdown step={span} />
                      </div>
                    );
                  })}
                </div>
              </section>
            )}

            {/* ── 时间轴甘特图（真实执行时序，点击定位到 Span） ── */}
            {childSpans.length > 0 && <section>
              <h2 className="text-sm font-semibold text-slate-700 mb-3">二、实际执行步骤 · 时间分布</h2>
              <div className="bg-white border border-slate-200 rounded-xl p-5">
                <FlameGraph
                  steps={filteredSpans}
                  totalMs={trace.duration_ms}
                  onStepClick={scrollToStep}
                  highlightStepId={highlightStepId}
                />
              </div>
            </section>}

            {/* ── Error Panel ── */}
            {hasError && (
              <section id="error-panel">
                <h2 className="text-xs font-medium text-red-500 uppercase tracking-wider mb-3">⚠️ 错误详情</h2>
                <div className="bg-red-50 border border-red-200 rounded-xl p-5 space-y-3">
                  <div className="flex items-start justify-between">
                    <div>
                      <p className="font-semibold text-red-700">{errorCode ? `错误码：${errorCode}` : hasErrorCode ? "错误码文本已隐藏" : "错误码：未采集"}</p>
                      <p className="text-sm text-red-600 mt-1">错误消息已隐藏；请通过受控服务端日志排查。</p>
                    </div>
                    <span className="text-xs text-red-400 font-mono">{formatTime(trace.timestamp)}</span>
                  </div>
                  <div className="grid grid-cols-3 gap-4 text-sm">
                    <div>
                      <span className="text-red-400 text-xs">Retry Count</span>
                      <p className="text-red-700 font-mono font-semibold">{typeof err.retry_count === "number" ? safeNum(err.retry_count) : "未采集"}</p>
                    </div>
                    <div>
                      <span className="text-red-400 text-xs">Error Node</span>
                      {errorStepId ? (
                        <button
                          onClick={() => scrollToStep(errorStepId)}
                          className="text-red-700 font-mono underline hover:text-red-900"
                        >
                          {errorStepId}
                        </button>
                      ) : (
                        <p className="text-red-700 font-mono">{safeRuntimeLabel(err.error_node, "未记录")}</p>
                      )}
                    </div>
                    <div>
                      <span className="text-red-400 text-xs">Status</span>
                      <p className="text-red-700 font-semibold">{getRunStatusLabel(runStatus)}</p>
                    </div>
                  </div>
                </div>
              </section>
            )}

            {/* ── Rerank Warning ── */}
            {rerankZero && (
              <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 flex items-start gap-3">
                <span className="text-amber-500 text-lg mt-0.5">⚠️</span>
                <div className="flex-1">
                  <p className="text-sm font-medium text-amber-800">Rerank 过滤掉全部文档</p>
                  <p className="text-xs text-amber-700 mt-1">本次执行记录显示重排后结果数为 0；查询与回答内容已隐藏。</p>
                </div>
                <button onClick={() => scrollToStep("rerank")} className="text-xs text-amber-700 hover:text-amber-900 underline shrink-0">
                  跳到 Span →
                </button>
              </div>
            )}

            {/* ── LLM 调用明细 ── */}
            <section>
              <h2 className="text-sm font-semibold text-slate-700 mb-3">二、实际执行步骤 · LLM 调用</h2>
              <LLMCallDetail steps={spans} />
            </section>

            {/* ── MultiQuery ── */}
            {mqSpan && mqSpan.metrics?.triggered === true && (
              <section>
                <h2 className="text-sm font-semibold text-slate-700 mb-3">二、实际执行步骤 · MultiQuery</h2>
                <div className="bg-white border border-slate-200 rounded-xl p-4">
                  <p className="text-sm text-slate-600">MultiQuery 已触发；查询与改写文本已隐藏。</p>
                </div>
              </section>
            )}

            {/* ── Faithfulness Detail ── */}
            {faithSpan && faithSpan.metrics?.claims && Number(faithSpan.metrics.claims) > 0 && (
              <section>
                <h2 className="text-sm font-semibold text-slate-700 mb-3">三、工程诊断 · 忠实度</h2>
                <div className="bg-white border border-slate-200 rounded-xl p-5">
                  <div className="grid grid-cols-4 gap-4 mb-4">
                    <div className="text-center"><p className="text-2xl font-bold text-slate-800">{Number(faithSpan.metrics.score ?? 1).toFixed(2)}</p><p className="text-[10px] text-slate-400 mt-1">忠实度评分</p></div>
                    <div className="text-center"><p className="text-2xl font-bold text-slate-800">{safeNum(faithSpan.metrics.claims)}</p><p className="text-[10px] text-slate-400 mt-1">Claims</p></div>
                    <div className="text-center"><p className="text-2xl font-bold text-emerald-600">{safeNum(faithSpan.metrics.supported)}</p><p className="text-[10px] text-slate-400 mt-1">已支撑</p></div>
                    <div className="text-center"><p className="text-2xl font-bold text-red-500">{safeNum(faithSpan.metrics.unsupported)}</p><p className="text-[10px] text-slate-400 mt-1">未支撑</p></div>
                  </div>
                </div>
              </section>
            )}

            {/* ── Metadata ── */}
            <section>
              <h2 className="text-sm font-semibold text-slate-700 mb-3">三、工程诊断 · 运行元数据</h2>
              <div className="bg-white border border-slate-200 rounded-xl p-5">
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                  <div><span className="text-slate-500 text-xs">模型</span><p className="text-slate-800 font-mono">{trace.model?.name || "未采集"}</p></div>
                  <div><span className="text-slate-500 text-xs">Provider</span><p className="text-slate-800 font-mono">{trace.model?.provider || "未采集"}</p></div>
                  <div><span className="text-slate-500 text-xs">Workflow</span><p className="text-slate-800 font-mono">{safeRuntimeLabel(trace.workflow_name, "未采集")}</p></div>
                  <div><span className="text-slate-500 text-xs">实际记录 Span</span><p className="text-slate-800 font-mono">{spans.length}</p></div>
                  <div className="col-span-2 md:col-span-4">
                    <span className="text-slate-500 text-xs">Prompt 版本</span>
                    <div className="mt-1"><PromptVersionsBadge metadata={trace.metadata || {}} /></div>
                    {!Array.isArray(trace.metadata?.prompt_versions) && <p className="text-xs text-slate-500">未采集</p>}
                  </div>
                  <div>
                    <span className="text-slate-500 text-xs">开始时间</span>
                    <p className="text-slate-800 font-mono text-xs" title={formatTime(trace.timestamp)}>
                      {formatRelative(trace.timestamp)} · {formatTime(trace.timestamp)}
                    </p>
                  </div>
                </div>
              </div>
            </section>
          </div>

          {/* ── 右侧成本栏 ── */}
          <aside className="lg:col-span-1 space-y-5">
            <CostPanel trace={trace} />

            {/* SLA 卡片 */}
            {trace.sla && (
              <div className="bg-white border border-slate-200 rounded-xl p-5">
                <p className="text-[10px] uppercase tracking-widest text-slate-400 mb-2">SLA</p>
                <div className="space-y-2">
                  <div className="flex items-baseline justify-between">
                    <span className="text-xs text-slate-500">阈值</span>
                    <span className="font-mono text-slate-700">{trace.sla.threshold_ms}ms</span>
                  </div>
                  <div className="flex items-baseline justify-between">
                    <span className="text-xs text-slate-500">实际</span>
                    <span className={`font-mono ${durationColor(trace.duration_ms)}`}>{trace.duration_ms}ms</span>
                  </div>
                  <div className="relative h-2 bg-slate-100 rounded-full overflow-hidden">
                    <div className="absolute inset-y-0 left-0 bg-emerald-300" style={{ width: `${Math.min((trace.duration_ms / trace.sla.threshold_ms) * 100, 100)}%` }} />
                    <div className="absolute inset-y-0 left-0 w-px bg-red-500" style={{ left: "100%" }} />
                  </div>
                  <p className={`text-xs font-semibold ${trace.sla.breached ? "text-red-600" : "text-emerald-600"}`}>
                    {trace.sla.breached ? "❌ 已违反" : "✅ 合规"}
                  </p>
                </div>
              </div>
            )}

          </aside>
        </div>
      </div>
    </div>
  );
}
