"use client";

import { useState, useMemo, useRef, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import mockTraces from "@/mock/traces.json";
import TraceOverviewCard from "@/components/observability/trace/TraceOverviewCard";
import InputOutputPanel from "@/components/observability/trace/InputOutputPanel";
import StepTimeline from "@/components/observability/trace/StepTimeline";
import TraceBreadcrumb from "@/components/observability/trace/TraceBreadcrumb";
import FlameGraph from "@/components/observability/trace/FlameGraph";
import LLMCallDetail from "@/components/observability/trace/LLMCallDetail";
import CostPanel from "@/components/observability/trace/CostPanel";
import HttpBreakdown from "@/components/observability/trace/HttpBreakdown";
import { useToast } from "@/components/shared/Toast";
import {
  statusBadge,
  formatTime,
  formatRelative,
  formatBoth,
  stepColor,
  durationColor,
  TraceRecord,
} from "@/types/trace";

const typedTraces = mockTraces as unknown as TraceRecord[];

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
  const [highlightStepId, setHighlightStepId] = useState<string | null>(null);
  const [httpStepId, setHttpStepId] = useState<string | null>(null);
  const toast = useToast();

  const trace = typedTraces.find((t) => t.id === id);

  const parent = useMemo(() => trace?.parent_id ? typedTraces.find((t) => t.id === trace.parent_id) : null, [trace]);
  const children = useMemo(() => trace?.children_ids?.map((cid) => typedTraces.find((t) => t.id === cid)).filter(Boolean) as TraceRecord[] ?? [], [trace]);

  // P1-1: 跟踪所有活跃的 timer，组件卸载时统一清理
  const timersRef = useRef<Set<ReturnType<typeof setTimeout>>>(new Set());
  useEffect(() => {
    return () => {
      timersRef.current.forEach(clearTimeout);
      timersRef.current.clear();
    };
  }, []);

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
  // P1-2: 类型守卫，防止 error_node 为对象时把对象当字符串传给 scrollToStep
  const errorStepId = typeof err.error_node === "string" ? err.error_node : null;
  const toggleStep = (stepId: string) => {
    const next = new Set(expandedSteps);
    next.has(stepId) ? next.delete(stepId) : next.add(stepId);
    setExpandedSteps(next);
  };

  const scrollToStep = (stepId: string) => {
    setHighlightStepId(stepId);
    // 清理上一次未完成的 timer（避免快速点击叠加）
    timersRef.current.forEach(clearTimeout);
    timersRef.current.clear();

    const t1 = setTimeout(() => {
      const el = document.getElementById(`step-${stepId}`);
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

  const handleRetry = () => {
    toast.info(`重新执行 trace ${trace.id.slice(0, 12)}…（待对接 API）`);
  };

  const handleExport = () => {
    const blob = new Blob([JSON.stringify(trace, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `trace-${trace.id}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="max-w-[1440px] mx-auto px-6 py-6 space-y-5">
        {/* ── Breadcrumb ── */}
        <TraceBreadcrumb
          crumbs={[
            { label: "可观测中心", href: "/observability" },
            { label: "链路追踪", href: "/observability/traces" },
            ...(parent ? [{ label: `父 · ${parent.id.slice(0, 8)}`, href: `/observability/traces/${parent.id}` }] : []),
            { label: trace.id.slice(0, 12) },
          ]}
        />

        {/* ── Header ── */}
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-3 flex-wrap">
            <button onClick={() => router.push("/observability/traces")} className="text-slate-400 hover:text-slate-600 text-sm">← 返回</button>
            <span className="font-mono text-slate-700 font-semibold">{trace.id.slice(0, 12)}</span>
            <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold ${badge.bg}`}>{badge.label}</span>
            {trace.sla?.breached && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-red-100 text-red-700">
                ⚠️ SLA 违反 ({trace.sla.threshold_ms}ms)
              </span>
            )}
            <button onClick={() => navigator.clipboard.writeText(trace.id)} className="text-xs text-slate-400 hover:text-slate-600 border border-slate-200 rounded px-2 py-0.5">📋 复制 ID</button>
            <span className="text-[10px] text-slate-400" title={formatTime(trace.timestamp)}>{formatRelative(trace.timestamp)}</span>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={handleRetry} className="text-xs text-slate-500 border border-slate-200 rounded px-3 py-1 hover:bg-slate-100">🔄 重新执行</button>
            <button onClick={() => setShowJson(!showJson)} className="text-xs text-slate-500 border border-slate-200 rounded px-3 py-1 hover:bg-slate-100">{showJson ? "隐藏" : "{} JSON"}</button>
            <button onClick={handleExport} className="text-xs text-slate-500 border border-slate-200 rounded px-3 py-1 hover:bg-slate-100">📥 导出</button>
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
                  <Link href={`/observability/traces/${parent.id}`} className="font-mono text-violet-700 hover:underline">
                    {parent.id.slice(0, 16)}
                  </Link>
                  <span className="text-slate-400 truncate">{parent.question}</span>
                </div>
              )}
              {children.length > 0 && (
                <div className="flex items-start gap-2">
                  <span className="text-slate-500 shrink-0">子 ({children.length}):</span>
                  <div className="flex flex-wrap gap-1.5">
                    {children.map((c) => (
                      <Link
                        key={c.id}
                        href={`/observability/traces/${c.id}`}
                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-white border border-violet-200 hover:border-violet-400 transition-colors"
                      >
                        <span className="font-mono text-violet-700">{c.id.slice(0, 8)}</span>
                        <span className="text-slate-400">·</span>
                        <span className="text-slate-600 truncate max-w-[200px]">{c.question}</span>
                      </Link>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── JSON Drawer ── */}
        {showJson && (
          <div className="bg-slate-900 border border-slate-700 rounded-xl p-4 overflow-x-auto">
            <pre className="text-xs text-emerald-400 font-mono leading-relaxed whitespace-pre">{JSON.stringify(trace, null, 2)}</pre>
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-4 gap-5">
          {/* 主区域 */}
          <div className="lg:col-span-3 space-y-5">
            {/* ── Overview ── */}
            <section>
              <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">概览</h2>
              <TraceOverviewCard trace={trace} />
            </section>

            {/* ── 火焰图 ── */}
            <section>
              <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">🔥 耗时火焰图</h2>
              <div className="bg-white border border-slate-200 rounded-xl p-5">
                <FlameGraph
                  steps={trace.steps}
                  totalMs={trace.duration_ms}
                  onStepClick={scrollToStep}
                  highlightStepId={highlightStepId}
                />
              </div>
            </section>

            {/* ── Error Panel ── */}
            {hasError && (
              <section id="error-panel">
                <h2 className="text-xs font-medium text-red-500 uppercase tracking-wider mb-3">⚠️ 错误详情</h2>
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
                        <button
                          onClick={() => scrollToStep(errorStepId)}
                          className="text-red-700 font-mono underline hover:text-red-900"
                        >
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

            {/* ── Rerank Warning ── */}
            {rerankZero && (
              <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 flex items-start gap-3">
                <span className="text-amber-500 text-lg mt-0.5">⚠️</span>
                <div className="flex-1">
                  <p className="text-sm font-medium text-amber-800">Rerank 过滤掉全部文档</p>
                  <p className="text-xs text-amber-600 mt-1">阈值 {String(rerankStep?.metrics?.threshold ?? "0.3")} 导致全部检索结果被过滤。回答可能基于模型固有参数知识，未引用实际数据库。</p>
                </div>
                <button onClick={() => scrollToStep("rerank")} className="text-xs text-amber-700 hover:text-amber-900 underline shrink-0">
                  跳到 Step →
                </button>
              </div>
            )}

            {/* ── I/O ── */}
            <section>
              <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">输入 / 输出</h2>
              <InputOutputPanel question={trace.question} answer={trace.answer_preview} error={trace.error} />
            </section>

            {/* ── Timeline ── */}
            <section>
              <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">⏱ 步骤时间线（点击 step 展开 HTTP 拆分）</h2>
              <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-3">
                <StepTimeline steps={trace.steps} totalMs={trace.duration_ms} onToggle={toggleStep} expanded={expandedSteps} highlightStepId={highlightStepId} />

                {/* 展开后追加 HTTP 拆分 */}
                {Array.from(expandedSteps).map((sid) => {
                  const step = trace.steps.find((s) => s.id === sid);
                  if (!step || !step.http_breakdown) return null;
                  return (
                    <div key={`http-${sid}`} className="border-t border-slate-100 pt-3">
                      <p className="text-[10px] uppercase tracking-wider text-slate-400 mb-2">📡 {step.label} · HTTP 耗时拆分</p>
                      <HttpBreakdown step={step} />
                    </div>
                  );
                })}
              </div>
            </section>

            {/* ── LLM 调用明细 ── */}
            <section>
              <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">🤖 LLM 调用明细</h2>
              <LLMCallDetail steps={trace.steps} />
            </section>

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
                  <div>
                    <span className="text-slate-400 text-xs">Session ID</span>
                    <Link href={`/observability/sessions/${trace.session_id}`} className="block text-violet-600 hover:underline font-mono text-xs">
                      {trace.session_id ?? "--"}
                    </Link>
                  </div>
                  <div><span className="text-slate-400 text-xs">KB</span><p className="text-slate-700 font-mono">{String(trace.metadata?.kb_id ?? "--")}</p></div>
                  <div><span className="text-slate-400 text-xs">Temperature</span><p className="text-slate-700 font-mono">{String(trace.metadata?.temperature ?? "0.1")}</p></div>
                  <div><span className="text-slate-400 text-xs">Max Tokens</span><p className="text-slate-700 font-mono">{String(trace.metadata?.max_tokens ?? "4096")}</p></div>
                  <div><span className="text-slate-400 text-xs">Request ID</span><p className="text-slate-700 font-mono text-xs">{trace.request_id}</p></div>
                  <div>
                    <span className="text-slate-400 text-xs">时间</span>
                    <p className="text-slate-700 font-mono text-xs" title={formatTime(trace.timestamp)}>
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

            {/* Session 链接卡片 */}
            {trace.session && (
              <Link href={`/observability/sessions/${trace.session_id}`} className="block bg-violet-50 border border-violet-200 hover:border-violet-400 rounded-xl p-5 transition-colors">
                <p className="text-[10px] uppercase tracking-widest text-violet-700 mb-2">📂 Session</p>
                <p className="font-mono text-sm text-slate-700">{trace.session_id}</p>
                <p className="text-xs text-slate-500 mt-2">
                  用户 <span className="font-medium">{trace.session.user_name ?? "--"}</span>
                </p>
                <p className="text-xs text-slate-500 mt-1">
                  共 <span className="font-mono font-semibold text-violet-700">{trace.session.trace_count}</span> 次 trace
                </p>
                <p className="text-[10px] text-violet-600 mt-3">查看 Session 详情 →</p>
              </Link>
            )}
          </aside>
        </div>
      </div>
    </div>
  );
}