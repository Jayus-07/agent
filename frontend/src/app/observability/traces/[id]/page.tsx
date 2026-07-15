"use client";

import { useParams, useRouter } from "next/navigation";
import mockTraces from "@/mock/traces.json";
import TraceOverviewCard from "@/components/observability/trace/TraceOverviewCard";
import InputOutputPanel from "@/components/observability/trace/InputOutputPanel";
import StepTimeline from "@/components/observability/trace/StepTimeline";

const typedTraces = mockTraces as unknown as import("@/types/trace").TraceRecord[];

export default function TraceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();

  const trace = typedTraces.find((t) => t.id === id);

  if (!trace) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="text-center">
          <p className="text-slate-400 text-lg font-mono">Trace {id} 不存在</p>
          <button onClick={() => router.push("/observability/traces")} className="mt-3 text-sm text-violet-600 hover:text-violet-500">
            ← 返回列表
          </button>
        </div>
      </div>
    );
  }

  const hasError = trace.error && Object.keys(trace.error).length > 0;
  const rerankStep = trace.steps.find((s) => s.id === "rerank");
  const rerankZero = rerankStep && Number(rerankStep.metrics?.output_docs ?? -1) === 0;

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="max-w-[1440px] mx-auto px-6 py-6 space-y-5">
        {/* Breadcrumb */}
        <div className="flex items-center gap-2 text-sm">
          <button onClick={() => router.push("/observability/traces")} className="text-slate-400 hover:text-slate-600 transition-colors">
            链路追踪
          </button>
          <span className="text-slate-300">/</span>
          <span className="font-mono text-slate-700 font-medium">{trace.id.slice(0, 12)}</span>
          {hasError && <span className="text-xs bg-red-100 text-red-600 rounded px-2 py-0.5 font-medium">错误</span>}
        </div>

        {/* Overview */}
        <section>
          <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">概览</h2>
          <TraceOverviewCard trace={trace} />
        </section>

        {/* I/O */}
        <section>
          <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">输入 / 输出</h2>
          <InputOutputPanel question={trace.question} answer={trace.answer_preview} error={trace.error} />
        </section>

        {/* Rerank warning */}
        {rerankZero && (
          <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 flex items-start gap-3">
            <span className="text-amber-500 text-lg mt-0.5">⚠️</span>
            <div>
              <p className="text-sm font-medium text-amber-800">Rerank 过滤掉全部文档</p>
              <p className="text-xs text-amber-600 mt-1">
                阈值 {rerankStep?.metrics?.threshold ?? "0.3"} 导致 {rerankStep?.metrics?.input_docs ?? "?"} 篇检索结果全部被过滤。
                回答可能基于模型固有参数知识，未引用实际数据库。
              </p>
            </div>
          </div>
        )}

        {/* Timeline */}
        <section>
          <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">步骤时间线</h2>
          <div className="bg-white border border-slate-200 rounded-xl p-5">
            <StepTimeline steps={trace.steps} totalMs={trace.duration_ms} />
          </div>
        </section>

        {/* Faithfulness detail */}
        {trace.steps.find((s) => s.id === "faithfulness" && s.metrics?.claims && Number(s.metrics.claims) > 0) && (
          <section>
            <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">忠实度检测</h2>
            <div className="bg-white border border-slate-200 rounded-xl p-5">
              <div className="grid grid-cols-3 gap-4">
                <div className="text-center">
                  <p className="text-2xl font-bold text-slate-800">
                    {Number(trace.steps.find((s) => s.id === "faithfulness")?.metrics?.score ?? 1).toFixed(2)}
                  </p>
                  <p className="text-[10px] text-slate-400 mt-1">忠实度评分</p>
                </div>
                <div className="text-center">
                  <p className="text-2xl font-bold text-emerald-600">
                    {safeNum(trace.steps.find((s) => s.id === "faithfulness")?.metrics?.supported, "0")}
                  </p>
                  <p className="text-[10px] text-slate-400 mt-1">已支撑 Claims</p>
                </div>
                <div className="text-center">
                  <p className="text-2xl font-bold text-red-500">
                    {safeNum(trace.steps.find((s) => s.id === "faithfulness")?.metrics?.unsupported, "0")}
                  </p>
                  <p className="text-[10px] text-slate-400 mt-1">未支撑 Claims</p>
                </div>
              </div>
            </div>
          </section>
        )}

        {/* Metadata */}
        <section>
          <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">元数据</h2>
          <div className="bg-white border border-slate-200 rounded-xl p-5">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
              <div>
                <span className="text-slate-400 text-xs">Model</span>
                <p className="text-slate-700 font-mono">{trace.model?.name ?? "--"}</p>
              </div>
              <div>
                <span className="text-slate-400 text-xs">Provider</span>
                <p className="text-slate-700 font-mono">{trace.model?.provider ?? "--"}</p>
              </div>
              <div>
                <span className="text-slate-400 text-xs">Session ID</span>
                <p className="text-slate-700 font-mono text-xs">{trace.session_id ?? "--"}</p>
              </div>
              <div>
                <span className="text-slate-400 text-xs">KB</span>
                <p className="text-slate-700 font-mono">{String(trace.metadata?.kb_id ?? "--")}</p>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

function safeNum(v: unknown, fallback = "--"): string {
  if (v === undefined || v === null) return fallback;
  const n = Number(v);
  return isNaN(n) ? fallback : String(n);
}
