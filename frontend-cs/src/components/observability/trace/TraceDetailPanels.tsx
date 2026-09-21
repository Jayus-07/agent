"use client";

import { useState, useMemo, useRef, useEffect } from "react";
import TraceOverviewCard from "./TraceOverviewCard";
import InputOutputPanel from "./InputOutputPanel";
import StepTimeline from "./StepTimeline";
import FlameGraph from "./FlameGraph";
import LLMCallDetail from "./LLMCallDetail";
import CostPanel from "./CostPanel";
import HttpBreakdown from "./HttpBreakdown";
import SpanTypeSummary from "./SpanTypeSummary";
import SpanTypeFilter from "./SpanTypeFilter";
import GraphTopology from "./GraphTopology";
import PromptVersionsBadge from "./PromptVersionsBadge";
import AddToEvalModal from "./AddToEvalModal";
import {
  statusBadge,
  formatTime,
  formatRelative,
  formatBoth,
  durationColor,
  TraceRecord,
  Span,
  safeNum,
} from "@/types/trace";

interface Props {
  trace: TraceRecord;
}

export default function TraceDetailPanels({ trace }: Props) {
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());
  const [jsonExpanded, setJsonExpanded] = useState<Set<string>>(new Set());
  const [highlightStepId, setHighlightStepId] = useState<string | null>(null);
  const [activeSpanTypes, setActiveSpanTypes] = useState<Set<string>>(new Set());
  const [autoExpandLarge, setAutoExpandLarge] = useState(true);
  const [showEvalModal, setShowEvalModal] = useState(false);

  const timersRef = useRef<Set<ReturnType<typeof setTimeout>>>(new Set());
  useEffect(() => {
    return () => {
      timersRef.current.forEach(clearTimeout);
      timersRef.current.clear();
    };
  }, []);

  const spans = (trace?.spans || []) as Span[];

  const hasError = trace.error && Object.keys(trace.error).length > 0;
  const err = trace.error as Record<string, unknown>;
  const stat = hasError || trace.status === "error" ? "error" : trace.status === "timeout" ? "timeout" : "success";
  const badge = statusBadge(stat);

  const rerankSpan = spans.find((s) => s.type === "rerank" || s.id === "rerank");
  const rerankZero = rerankSpan && Number(rerankSpan.metrics?.output_docs ?? -1) === 0;
  const faithSpan = spans.find((s) => s.id === "faithfulness" || s.name === "Faithfulness");
  const mqSpan = spans.find((s) => s.id === "mq_check" || s.name === "MultiQuery");
  const errorStepId = typeof err.error_node === "string" ? err.error_node : null;

  const filteredSpans = activeSpanTypes.size === 0
    ? spans
    : spans.filter((s) => activeSpanTypes.has(s.type));

  const findSpan = (id: string) => spans.find((s) => s.id === id);

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

  const toggleStep = (spanId: string) => {
    const next = new Set(expandedSteps);
    next.has(spanId) ? next.delete(spanId) : next.add(spanId);
    setExpandedSteps(next);
  };

  return (
    <>
    <div className="grid grid-cols-1 lg:grid-cols-4 gap-5">
      <div className="lg:col-span-3 space-y-5">
        {/* Overview */}
        <section>
          <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">概览</h2>
          <TraceOverviewCard trace={trace} />
          <div className="mt-3">
            <PromptVersionsBadge metadata={trace.metadata} />
          </div>
        </section>

        {/* LangGraph DAG */}
        {trace.graph && (
          <section>
            <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">LangGraph 图拓扑</h2>
            <GraphTopology
              nodes={trace.graph.nodes}
              edges={trace.graph.edges}
              spans={spans}
              loopCount={trace.graph.loop_count}
              maxLoops={trace.graph.max_loops}
              degradationTriggered={trace.graph.degradation_triggered}
              onNodeClick={scrollToStep}
            />
          </section>
        )}

        {/* Span Duration Distribution */}
        {spans.length > 0 && (
          <section>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider">Span 耗时分布</h2>
              <label className="flex items-center gap-2 text-[11px] text-slate-500 cursor-pointer">
                <input type="checkbox" checked={autoExpandLarge} onChange={e => setAutoExpandLarge(e.target.checked)} className="rounded" />
                <span>自动展开 &gt;1s 的步骤</span>
              </label>
            </div>
            <div className="bg-white border border-slate-200 rounded-xl p-4">
              <StepTimeline
                steps={filteredSpans}
                totalMs={trace.duration_ms}
                onToggle={toggleStep}
                expanded={expandedSteps}
                jsonExpanded={jsonExpanded}
                onJsonToggle={(id) => {
                  const next = new Set(jsonExpanded);
                  next.has(id) ? next.delete(id) : next.add(id);
                  setJsonExpanded(next);
                }}
                highlightStepId={highlightStepId}
                autoExpandLarge={autoExpandLarge}
              />
            </div>
          </section>
        )}

        {/* Flame Graph */}
        <section>
          <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">耗时火焰图</h2>
          <div className="bg-white border border-slate-200 rounded-xl p-5">
            <FlameGraph
              steps={filteredSpans}
              totalMs={trace.duration_ms}
              onStepClick={scrollToStep}
              highlightStepId={highlightStepId}
            />
          </div>
        </section>

        {/* Error Panel */}
        {hasError && (
          <section>
            <h2 className="text-xs font-medium text-red-500 uppercase tracking-wider mb-3">错误详情</h2>
            <div className="bg-red-50 border border-red-200 rounded-xl p-5 space-y-3">
              <div className="flex items-start justify-between">
                <div>
                  <p className="font-semibold text-red-700">{String(err.code ?? "ERROR")}</p>
                  <p className="text-sm text-red-600 mt-1">{String(err.message ?? "")}</p>
                </div>
                <span className="text-xs text-red-400 font-mono">{formatBoth(trace.timestamp)}</span>
              </div>
              <div className="grid grid-cols-3 gap-4 text-sm">
                <div>
                  <span className="text-red-400 text-xs">Retry Count</span>
                  <p className="text-red-700 font-mono font-semibold">{safeNum(err.retry_count, "0")}</p>
                </div>
                <div>
                  <span className="text-red-400 text-xs">Error Node</span>
                  {errorStepId ? (
                    <button onClick={() => scrollToStep(errorStepId)} className="text-red-700 font-mono underline hover:text-red-900">
                      {errorStepId}
                    </button>
                  ) : (
                    <p className="text-red-700 font-mono">{String(err.error_node ?? "--")}</p>
                  )}
                </div>
                <div>
                  <span className="text-red-400 text-xs">Status</span>
                  <p className="text-red-700 font-semibold">{badge.label}</p>
                </div>
              </div>
            </div>
          </section>
        )}

        {/* Rerank Warning */}
        {rerankZero && (
          <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 flex items-start gap-3">
            <span className="text-amber-500 text-lg mt-0.5">&#x26A0;</span>
            <div className="flex-1">
              <p className="text-sm font-medium text-amber-800">Rerank 过滤掉全部文档</p>
              <p className="text-xs text-amber-600 mt-1">阈值 {String(rerankSpan?.metrics?.threshold ?? "0.3")} 导致全部检索结果被过滤。</p>
            </div>
            <button onClick={() => scrollToStep("rerank")} className="text-xs text-amber-700 hover:text-amber-900 underline shrink-0">
              跳到 Span
            </button>
          </div>
        )}

        {/* I/O */}
        <section>
          <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">输入 / 输出</h2>
          <InputOutputPanel question={trace.question} answer={trace.answer_preview} error={trace.error} />
        </section>

        {/* Span Timeline */}
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider">Span 时间线</h2>
            <span className="text-[10px] text-slate-400">
              {filteredSpans.length}/{spans.length} span
              {activeSpanTypes.size > 0 && (
                <button onClick={() => setActiveSpanTypes(new Set())} className="ml-2 text-violet-500 hover:text-violet-700">
                  清除过滤
                </button>
              )}
            </span>
          </div>
          <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-3">
            <SpanTypeFilter spans={spans} activeTypes={activeSpanTypes} onChange={setActiveSpanTypes} />
            <div className="border-t border-slate-100 pt-3">
              <StepTimeline
                steps={filteredSpans}
                totalMs={trace.duration_ms}
                onToggle={toggleStep}
                expanded={expandedSteps}
                jsonExpanded={jsonExpanded}
                onJsonToggle={(id) => {
                  const n = new Set(jsonExpanded);
                  n.has(id) ? n.delete(id) : n.add(id);
                  setJsonExpanded(n);
                }}
                highlightStepId={highlightStepId}
              />
            </div>
            {Array.from(expandedSteps).map((sid) => {
              const span = findSpan(sid);
              if (!span || !span.http_breakdown) return null;
              return (
                <div key={`http-${sid}`} className="border-t border-slate-100 pt-3">
                  <p className="text-[10px] uppercase tracking-wider text-slate-400 mb-2">{span.name} - HTTP 耗时拆分</p>
                  <HttpBreakdown step={span} />
                </div>
              );
            })}
          </div>
        </section>

        {/* LLM Calls */}
        <section>
          <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">LLM 调用明细</h2>
          <LLMCallDetail steps={spans} />
        </section>

        {/* Metadata */}
        <section>
          <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">元数据</h2>
          <div className="bg-white border border-slate-200 rounded-xl p-5">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
              <div><span className="text-slate-400 text-xs">Model</span><p className="text-slate-700 font-mono">{trace.model?.name ?? "--"}</p></div>
              <div><span className="text-slate-400 text-xs">Provider</span><p className="text-slate-700 font-mono">{trace.model?.provider ?? "--"}</p></div>
              <div><span className="text-slate-400 text-xs">Session ID</span><p className="text-slate-700 font-mono text-xs">{trace.session_id ?? "--"}</p></div>
              <div><span className="text-slate-400 text-xs">Workflow</span><p className="text-slate-700 font-mono">{trace.workflow_name ?? "--"}</p></div>
              <div><span className="text-slate-400 text-xs">Spans</span><p className="text-slate-700 font-mono">{spans.length}</p></div>
              <div><span className="text-slate-400 text-xs">Duration</span><p className={`font-mono ${durationColor(trace.duration_ms)}`}>{trace.duration_ms}ms</p></div>
              <div>
                <span className="text-slate-400 text-xs">时间</span>
                <p className="text-slate-700 font-mono text-xs" title={formatTime(trace.timestamp)}>
                  {formatRelative(trace.timestamp)}
                </p>
              </div>
            </div>
          </div>
        </section>
      </div>

      {/* Right sidebar */}
      <aside className="lg:col-span-1 space-y-5">
        <CostPanel trace={trace} />

        <button
          onClick={() => setShowEvalModal(true)}
          className="w-full py-2.5 rounded-xl bg-violet-600 text-white text-sm font-medium hover:bg-violet-700 transition-colors"
        >
          加入评测集
        </button>

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
              </div>
              <p className={`text-xs font-semibold ${trace.sla.breached ? "text-red-600" : "text-emerald-600"}`}>
                {trace.sla.breached ? "已违反" : "合规"}
              </p>
            </div>
          </div>
        )}
      </aside>
    </div>

    {showEvalModal && (
      <AddToEvalModal
        traceId={trace.id}
        question={trace.question}
        onClose={() => setShowEvalModal(false)}
      />
    )}
  </>
  );
}
