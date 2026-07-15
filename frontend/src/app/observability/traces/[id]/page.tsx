"use client";

import { useState } from "react";
import { useParams, useRouter } from "next/navigation";
import mockTraces from "@/mock/traces.json";
import TraceOverviewCard from "@/components/observability/trace/TraceOverviewCard";
import InputOutputPanel from "@/components/observability/trace/InputOutputPanel";
import StepTimeline from "@/components/observability/trace/StepTimeline";
import { statusBadge, formatTime, stepColor } from "@/types/trace";

const typedTraces = mockTraces as unknown as import("@/types/trace").TraceRecord[];

function safeNum(v: unknown, fallback = "--"): string {
  if (v === undefined || v === null) return fallback;
  const n = Number(v);
  return isNaN(n) ? fallback : String(n);
}

export default function TraceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [showJson, setShowJson] = useState(false);
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());

  const trace = typedTraces.find((t) => t.id === id);
  if (!trace) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="text-center">
          <p className="text-slate-400 text-lg font-mono">Trace {id} 不存在</p>
          <button onClick={() => router.push("/observability/traces")} className="mt-3 text-sm text-violet-600 hover:text-violet-500">← 返回列表</button>
        </div>
      </div>
    );
  }

  const hasError = trace.error && Object.keys(trace.error).length > 0;
  const err = trace.error as Record<string, unknown>;
  const stat = hasError || trace.status === "error" ? "error" : trace.status === "timeout" ? "timeout" : "success";
  const badge = statusBadge(stat);
  const rerankStep = trace.steps.find((s) => s.id === "rerank");
  const rerankZero = rerankStep && Number(rerankStep.metrics?.output_docs ?? -1) === 0;
  const faithStep = trace.steps.find((s) => s.id === "faithfulness");
  const mqStep = trace.steps.find((s) => s.id === "mq_check");
  const toggleStep = (id: string) => {
    const next = new Set(expandedSteps);
    next.has(id) ? next.delete(id) : next.add(id);
    setExpandedSteps(next);
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="max-w-[1440px] mx-auto px-6 py-6 space-y-5">
        {/* ── Header ── */}
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-3">
            <button onClick={() => router.push("/observability/traces")} className="text-slate-400 hover:text-slate-600 text-sm">← 返回</button>
            <span className="font-mono text-slate-700 font-semibold">{trace.id.slice(0, 12)}</span>
            <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold ${badge.bg}`}>{badge.label}</span>
            <button onClick={() => navigator.clipboard.writeText(trace.id)} className="text-xs text-slate-400 hover:text-slate-600 border border-slate-200 rounded px-2 py-0.5">📋 复制</button>
          </div>
          <div className="flex items-center gap-2">
            <button className="text-xs text-slate-400 border border-slate-200 rounded px-3 py-1 hover:bg-slate-100">🔄 重新执行</button>
            <button onClick={() => setShowJson(!showJson)} className="text-xs text-slate-400 border border-slate-200 rounded px-3 py-1 hover:bg-slate-100">{showJson ? "隐藏" : "{} JSON"}</button>
            <button className="text-xs text-slate-400 border border-slate-200 rounded px-3 py-1 hover:bg-slate-100">📥 导出</button>
          </div>
        </div>

        {/* ── JSON Drawer ── */}
        {showJson && (
          <div className="bg-slate-900 border border-slate-700 rounded-xl p-4 overflow-x-auto">
            <pre className="text-xs text-emerald-400 font-mono leading-relaxed whitespace-pre">{JSON.stringify(trace, null, 2)}</pre>
          </div>
        )}

        {/* ── Overview ── */}
        <section>
          <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">概览</h2>
          <TraceOverviewCard trace={trace} />
        </section>

        {/* ── I/O ── */}
        <section>
          <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">输入 / 输出</h2>
          <InputOutputPanel question={trace.question} answer={trace.answer_preview} error={trace.error} />
        </section>

        {/* ── Error Panel ── */}
        {hasError && (
          <section>
            <h2 className="text-xs font-medium text-red-500 uppercase tracking-wider mb-3">⚠️ 错误详情</h2>
            <div className="bg-red-50 border border-red-200 rounded-xl p-5 space-y-3">
              <div className="flex items-start justify-between">
                <div>
                  <p className="font-semibold text-red-700">{String(err.code ?? "ERROR")}</p>
                  <p className="text-sm text-red-600 mt-1">{String(err.message ?? "")}</p>
                </div>
                <span className="text-xs text-red-400 font-mono">{formatTime(trace.timestamp)}</span>
              </div>
              <div className="grid grid-cols-3 gap-4 text-sm">
                <div><span className="text-red-400 text-xs">Retry Count</span><p className="text-red-700 font-mono font-semibold">{safeNum(err.retry_count, "0")}</p></div>
                <div><span className="text-red-400 text-xs">Error Node</span><p className="text-red-700 font-mono">{String(err.error_node ?? "--")}</p></div>
                <div><span className="text-red-400 text-xs">Status</span><p className="text-red-700 font-semibold">{badge.label}</p></div>
              </div>
            </div>
          </section>
        )}

        {/* ── Rerank Warning ── */}
        {rerankZero && (
          <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 flex items-start gap-3">
            <span className="text-amber-500 text-lg mt-0.5">⚠️</span>
            <div>
              <p className="text-sm font-medium text-amber-800">Rerank 过滤掉全部文档</p>
              <p className="text-xs text-amber-600 mt-1">阈值 {String(rerankStep?.metrics?.threshold ?? "0.3")} 导致全部检索结果被过滤。回答可能基于模型固有参数知识，未引用实际数据库。</p>
            </div>
          </div>
        )}

        {/* ── MultiQuery ── */}
        {mqStep && mqStep.metrics?.triggered === true && (
          <section>
            <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">🔀 MultiQuery 改写</h2>
            <div className="bg-white border border-slate-200 rounded-xl p-4">
              <div className="flex items-center gap-2 mb-3">
                <span className="text-[10px] text-slate-400 bg-slate-100 rounded px-2 py-0.5">原查询</span>
                <span className="text-sm text-slate-700 font-mono">{trace.question}</span>
              </div>
              <div className="space-y-1.5 ml-4 border-l-2 border-violet-200 pl-4">
                {trace.steps.find(s => s.id === "query_rewrite")?.metrics?.variants ? (
                  [...Array(Number(trace.steps.find(s => s.id === "query_rewrite")?.metrics?.variants ?? 0))].map((_, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <span className="text-[10px] text-violet-400">改写{i + 1}</span>
                      <span className="text-xs text-slate-500 font-mono">FBA退货变体 #{i + 1}</span>
                    </div>
                  ))
                ) : (
                  <p className="text-xs text-slate-400">改写已触发，变体详情需后端 step.output 字段支持</p>
                )}
              </div>
            </div>
          </section>
        )}

        {/* ── Timeline ── */}
        <section>
          <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">⏱ 步骤时间线</h2>
          <div className="bg-white border border-slate-200 rounded-xl p-5">
            <StepTimeline steps={trace.steps} totalMs={trace.duration_ms} onToggle={toggleStep} expanded={expandedSteps} />
          </div>
        </section>

        {/* ── Faithfulness Detail ── */}
        {faithStep && faithStep.metrics?.claims && Number(faithStep.metrics.claims) > 0 && (
          <section>
            <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">✅ 忠实度检测</h2>
            <div className="bg-white border border-slate-200 rounded-xl p-5">
              <div className="grid grid-cols-4 gap-4 mb-4">
                <div className="text-center"><p className="text-2xl font-bold text-slate-800">{Number(faithStep.metrics.score ?? 1).toFixed(2)}</p><p className="text-[10px] text-slate-400 mt-1">忠实度评分</p></div>
                <div className="text-center"><p className="text-2xl font-bold text-slate-800">{safeNum(faithStep.metrics.claims)}</p><p className="text-[10px] text-slate-400 mt-1">Claims</p></div>
                <div className="text-center"><p className="text-2xl font-bold text-emerald-600">{safeNum(faithStep.metrics.supported)}</p><p className="text-[10px] text-slate-400 mt-1">已支撑</p></div>
                <div className="text-center"><p className="text-2xl font-bold text-red-500">{safeNum(faithStep.metrics.unsupported)}</p><p className="text-[10px] text-slate-400 mt-1">未支撑</p></div>
              </div>
              {/* Claim-level examples */}
              {Number(faithStep.metrics.unsupported) > 0 && (
                <div className="border-t border-slate-100 pt-4 mt-2 space-y-2">
                  <p className="text-[10px] text-slate-400 uppercase tracking-wider">未支撑 Claim 示例</p>
                  {[...Array(Math.min(Number(faithStep.metrics.unsupported), 2))].map((_, i) => (
                    <div key={i} className="flex items-start gap-3 bg-red-50 border border-red-100 rounded-lg p-3">
                      <span className="text-red-400 text-xs mt-0.5">✗</span>
                      <div>
                        <p className="text-xs text-red-700">Claim #{i + 1}: 文档中未找到对应证据</p>
                        <p className="text-[10px] text-red-400 mt-0.5">文档不支持此声明</p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>
        )}

        {/* ── Metadata ── */}
        <section>
          <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">📋 元数据</h2>
          <div className="bg-white border border-slate-200 rounded-xl p-5">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
              <div><span className="text-slate-400 text-xs">Model</span><p className="text-slate-700 font-mono">{trace.model?.name ?? "--"}</p></div>
              <div><span className="text-slate-400 text-xs">Provider</span><p className="text-slate-700 font-mono">{trace.model?.provider ?? "--"}</p></div>
              <div><span className="text-slate-400 text-xs">Session ID</span><p className="text-slate-700 font-mono text-xs">{trace.session_id ?? "--"}</p></div>
              <div><span className="text-slate-400 text-xs">KB</span><p className="text-slate-700 font-mono">{String(trace.metadata?.kb_id ?? "--")}</p></div>
              <div><span className="text-slate-400 text-xs">Temperature</span><p className="text-slate-700 font-mono">{String(trace.metadata?.temperature ?? "0.1")}</p></div>
              <div><span className="text-slate-400 text-xs">Max Tokens</span><p className="text-slate-700 font-mono">{String(trace.metadata?.max_tokens ?? "4096")}</p></div>
              <div><span className="text-slate-400 text-xs">Request ID</span><p className="text-slate-700 font-mono text-xs">{trace.request_id}</p></div>
              <div><span className="text-slate-400 text-xs">时间</span><p className="text-slate-700 font-mono text-xs">{formatTime(trace.timestamp)}</p></div>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
