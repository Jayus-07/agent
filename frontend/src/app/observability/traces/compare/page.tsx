"use client";

import { useMemo, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import Link from "next/link";
import mockTraces from "@/mock/traces.json";
import TraceBreadcrumb from "@/components/observability/trace/TraceBreadcrumb";
import FlameGraph from "@/components/observability/trace/FlameGraph";
import {
  TraceRecord,
  formatTime,
  formatCost,
  durationColor,
  statusBadge,
} from "@/types/trace";

const typedTraces = mockTraces as unknown as TraceRecord[];

const COMMON_STEPS = [
  "query_rewrite", "hybrid_retrieval", "retrieval", "rerank",
  "llm_generate", "mq_check", "citation", "faithfulness",
];

export default function ComparePage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-slate-50 flex items-center justify-center text-sm text-slate-400">加载中…</div>}>
      <CompareInner />
    </Suspense>
  );
}

function CompareInner() {
  const params = useSearchParams();
  const router = useRouter();
  const ids = (params.get("ids") ?? "").split(",").filter(Boolean);

  const traces = useMemo(
    () => ids.map((id) => typedTraces.find((t) => t.id === id)).filter(Boolean) as TraceRecord[],
    [ids]
  );

  if (traces.length < 2) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="text-center">
          <p className="text-slate-400 text-lg">至少选择 2 个 trace 进行对比</p>
          <button onClick={() => router.push("/observability/traces")} className="mt-3 text-sm text-violet-600 hover:text-violet-500">← 返回列表</button>
        </div>
      </div>
    );
  }

  // 找最长耗时用于火焰图对齐
  const maxDuration = Math.max(...traces.map((t) => t.duration_ms));

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="max-w-[1440px] mx-auto px-6 py-6 space-y-5">
        <TraceBreadcrumb crumbs={[
          { label: "可观测中心", href: "/observability" },
          { label: "链路追踪", href: "/observability/traces" },
          { label: `对比 (${traces.length})` },
        ]} />

        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-slate-800">Trace 对比</h1>
            <p className="text-xs text-slate-500 mt-0.5">横向对比 {traces.length} 个 trace 的耗时、成本、Token、关键步骤</p>
          </div>
          <Link href="/observability/traces" className="text-xs text-violet-600 hover:text-violet-800">← 返回</Link>
        </div>

        {/* 总览对比 */}
        <section>
          <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">总览对比</h2>
          <div className={`grid gap-3`} style={{ gridTemplateColumns: `200px repeat(${traces.length}, minmax(0, 1fr))` }}>
            <div></div>
            {traces.map((t) => {
              const hasErr = t.error && Object.keys(t.error).length > 0;
              const stat = hasErr || t.status === "error" ? "error" : t.status === "timeout" || t.duration_ms > 5000 ? "timeout" : "success";
              const badge = statusBadge(stat);
              return (
                <div key={t.id} className="bg-white border border-slate-200 rounded-xl p-4">
                  <div className="flex items-center justify-between mb-2">
                    <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold ${badge.bg}`}>{badge.label}</span>
                    <Link href={`/observability/traces/${t.id}`} className="text-[10px] text-violet-600 hover:text-violet-800">详情 →</Link>
                  </div>
                  <p className="font-mono text-xs text-slate-600">{t.id.slice(0, 12)}</p>
                  <p className="text-xs text-slate-700 line-clamp-2 mt-1" title={t.question}>{t.question}</p>
                </div>
              );
            })}

            <CompareRow label="耗时" values={traces.map((t) => ({
              text: `${t.duration_ms}ms`,
              color: durationColor(t.duration_ms),
              bar: (t.duration_ms / maxDuration) * 100,
            }))} />

            <CompareRow label="Token" values={traces.map((t) => ({
              text: String(t.usage?.total_tokens ?? 0),
              color: "text-slate-700",
              bar: ((t.usage?.total_tokens ?? 0) / Math.max(...traces.map((x) => x.usage?.total_tokens ?? 1))) * 100,
            }))} />

            <CompareRow label="成本" values={traces.map((t) => ({
              text: formatCost(t.cost_usd),
              color: "text-emerald-600",
              bar: ((t.cost_usd ?? 0) / Math.max(...traces.map((x) => x.cost_usd ?? 0.000001))) * 100,
            }))} />

            <CompareRow label="P/C" values={traces.map((t) => ({
              text: `${t.usage?.prompt_tokens ?? 0}/${t.usage?.completion_tokens ?? 0}`,
              color: "text-slate-700",
              bar: 0,
            }))} />

            <CompareRow label="SLA" values={traces.map((t) => ({
              text: t.sla?.breached ? `❌ ${t.sla.threshold_ms}ms` : `✅ ${t.sla?.threshold_ms ?? "--"}ms`,
              color: t.sla?.breached ? "text-red-600" : "text-emerald-600",
              bar: 0,
            }))} />

            <CompareRow label="会话" values={traces.map((t) => ({
              text: t.session_id.slice(0, 12),
              color: "text-violet-600",
              bar: 0,
            }))} />

            <CompareRow label="时间" values={traces.map((t) => ({
              text: formatTime(t.timestamp),
              color: "text-slate-500",
              bar: 0,
            }))} />
          </div>
        </section>

        {/* 火焰图对比 */}
        <section>
          <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">🔥 耗时火焰图对比</h2>
          <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-5">
            {traces.map((t) => (
              <div key={t.id}>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs text-slate-600">{t.id.slice(0, 12)}</span>
                    <span className="text-xs text-slate-400 truncate max-w-[300px]">{t.question}</span>
                  </div>
                  <span className={`font-mono text-xs font-semibold ${durationColor(t.duration_ms)}`}>{t.duration_ms}ms</span>
                </div>
                <FlameGraph steps={t.steps} totalMs={t.duration_ms} />
              </div>
            ))}
          </div>
        </section>

        {/* 步骤对比表 */}
        <section>
          <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">⏱ 步骤耗时对比</h2>
          <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs font-medium text-slate-500">
                  <th className="py-3 px-4">步骤</th>
                  {traces.map((t) => (
                    <th key={t.id} className="py-3 px-4 text-right">{t.id.slice(0, 8)}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {COMMON_STEPS.map((sid) => {
                  const row = traces.map((t) => t.steps.find((s) => s.id === sid));
                  const anyExists = row.some((s) => s);
                  if (!anyExists) return null;
                  const maxMs = Math.max(...row.map((s) => s?.duration_ms ?? 0), 1);
                  return (
                    <tr key={sid}>
                      <td className="py-2 px-4 text-xs text-slate-600">{sid}</td>
                      {row.map((s, i) => {
                        if (!s) return <td key={i} className="py-2 px-4 text-right text-xs text-slate-300">--</td>;
                        const w = (s.duration_ms / maxMs) * 100;
                        return (
                          <td key={i} className="py-2 px-4">
                            <div className="flex items-center gap-2 justify-end">
                              <div className="w-20 h-2 bg-slate-100 rounded overflow-hidden">
                                <div
                                  className={`h-full ${s.status === "skipped" ? "bg-slate-300" : s.duration_ms > 1000 ? "bg-amber-500" : "bg-violet-500"}`}
                                  style={{ width: `${w}%` }}
                                />
                              </div>
                              <span className="font-mono text-xs text-slate-700 tabular-nums w-16 text-right">{s.duration_ms}ms</span>
                            </div>
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}

function CompareRow({ label, values }: { label: string; values: { text: string; color: string; bar: number }[] }) {
  return (
    <>
      <div className="flex items-center text-xs font-medium text-slate-500 uppercase tracking-wider">{label}</div>
      {values.map((v, i) => (
        <div key={i} className="bg-white border border-slate-200 rounded-xl px-3 py-2">
          <div className="flex items-center gap-2">
            {v.bar > 0 && (
              <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden min-w-[40px]">
                <div className="h-full bg-violet-400 rounded-full" style={{ width: `${Math.min(v.bar, 100)}%` }} />
              </div>
            )}
            <span className={`font-mono text-xs font-semibold tabular-nums ${v.color}`}>{v.text}</span>
          </div>
        </div>
      ))}
    </>
  );
}