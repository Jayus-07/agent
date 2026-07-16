"use client";

import { useEffect, useMemo, useState, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import Link from "next/link";
import { getTracesByIds } from "@/lib/observability/source";
import TraceBreadcrumb from "@/components/observability/trace/TraceBreadcrumb";
import FlameGraph from "@/components/observability/trace/FlameGraph";
import {
  TraceRecord,
  Span,
  SPAN_TYPE_LABELS,
  spanTypeColor,
  formatTime,
  formatCost,
  durationColor,
  statusBadge,
} from "@/types/trace";

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

  // 异步加载：批量按 ID 拉详情（并行）
  const [traces, setTraces] = useState<TraceRecord[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    if (ids.length === 0) {
      setTraces([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    getTracesByIds(ids).then((map) => {
      // 按 ids 顺序排，保证 UI 顺序稳定
      setTraces(ids.map((id) => map.get(id)).filter(Boolean) as TraceRecord[]);
      setLoading(false);
    });
  }, [ids]);

  const [viewMode, setViewMode] = useState<"span" | "type">("span");

  // 动态：按 type 分组聚合（预计算排序后的 entries，避免模板中 MapIterator 问题）
  const sortedTypeEntries = useMemo(() => {
    const groups = new Map<string, Span[][]>();
    for (const t of traces) {
      const spans = (t.spans || []).filter((s: Span) => s.parent_id !== null);
      for (const s of spans) {
        if (!groups.has(s.type)) groups.set(s.type, []);
      }
    }
    const entries = Array.from(groups.entries());
    for (const [type, rows] of entries) {
      for (const t of traces) {
        const match = (t.spans || []).filter((s: Span) => s.type === type && s.parent_id !== null);
        rows.push(match);
      }
    }
    return entries.sort(([, a], [, b]) => {
      const aMax = Math.max(...a.flat().map((s: Span) => s.duration_ms));
      const bMax = Math.max(...b.flat().map((s: Span) => s.duration_ms));
      return bMax - aMax;
    });
  }, [traces]);

  // 动态：收集所有非根 span，按 name 去重排序
  const allSpans = useMemo(() => {
    const seen = new Map<string, Span>();
    for (const t of traces) {
      for (const s of (t.spans || []).filter((s: Span) => s.parent_id !== null)) {
        if (!seen.has(s.id)) seen.set(s.id, s);
      }
    }
    return Array.from(seen.values()).sort((a, b) => b.duration_ms - a.duration_ms);
  }, [traces]);

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="text-center text-sm text-slate-400">加载中…</div>
      </div>
    );
  }

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
            <p className="text-xs text-slate-500 mt-0.5">
              横向对比 {traces.length} 个 trace ·
              {traces.map((t) => t.workflow_name).filter((v, i, a) => a.indexOf(v) === i).join(" / ")}
            </p>
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
                  <p className="text-[10px] text-slate-400 mt-0.5 font-mono">{t.workflow_name}</p>
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

            <CompareRow label="Span数" values={traces.map((t) => ({
              text: String((t.spans || []).filter(s => s.parent_id !== null).length),
              color: "text-slate-700",
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
                    <span className="text-[10px] text-slate-400 font-mono">{t.workflow_name}</span>
                    <span className="text-xs text-slate-400 truncate max-w-[300px]">{t.question}</span>
                  </div>
                  <span className={`font-mono text-xs font-semibold ${durationColor(t.duration_ms)}`}>{t.duration_ms}ms</span>
                </div>
                <FlameGraph steps={(t.spans || []).filter(s => s.parent_id !== null)} totalMs={t.duration_ms} />
              </div>
            ))}
          </div>
        </section>

        {/* Span 对比表（动态） */}
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider">
              {viewMode === "span" ? "⏱ 所有 Span 对比" : "📊 按 Type 分组对比"}
            </h2>
            <div className="flex items-center gap-1 bg-slate-100 rounded-lg p-0.5">
              <button
                onClick={() => setViewMode("span")}
                className={`text-[10px] px-3 py-1 rounded-md transition-colors ${viewMode === "span" ? "bg-white text-slate-700 shadow-sm" : "text-slate-400 hover:text-slate-600"}`}
              >
                按 Span
              </button>
              <button
                onClick={() => setViewMode("type")}
                className={`text-[10px] px-3 py-1 rounded-md transition-colors ${viewMode === "type" ? "bg-white text-slate-700 shadow-sm" : "text-slate-400 hover:text-slate-600"}`}
              >
                按 Type
              </button>
            </div>
          </div>
          <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs font-medium text-slate-500">
                  <th className="py-3 px-4">{viewMode === "span" ? "Span" : "Type"}</th>
                  {traces.map((t) => (
                    <th key={t.id} className="py-3 px-4 text-right">{t.id.slice(0, 8)}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {viewMode === "span"
                  ? /* 按 Span 对比 */
                    allSpans.map((span) => {
                      const row = traces.map((t) => (t.spans || []).find((s) => s.id === span.id));
                      const anyExists = row.some((s) => s);
                      if (!anyExists) return null;
                      const maxMs = Math.max(...row.map((s) => s?.duration_ms ?? 0), 1);
                      return (
                        <tr key={span.id}>
                          <td className="py-2 px-4">
                            <div className="flex items-center gap-2">
                              <span className={`inline-block w-2 h-2 rounded-full ${spanTypeColor(span.type)}`} />
                              <span className="text-xs text-slate-600">{span.name}</span>
                              <span className="text-[9px] text-slate-400">{SPAN_TYPE_LABELS[span.type] || span.type}</span>
                            </div>
                          </td>
                          {row.map((s, i) => {
                            if (!s) return <td key={i} className="py-2 px-4 text-right text-xs text-slate-300">--</td>;
                            const w = (s.duration_ms / maxMs) * 100;
                            return (
                              <td key={i} className="py-2 px-4">
                                <div className="flex items-center gap-2 justify-end">
                                  <div className="w-16 h-2 bg-slate-100 rounded overflow-hidden">
                                    <div
                                      className={`h-full ${s.status === "skipped" ? "bg-slate-300" : spanTypeColor(s.type)}`}
                                      style={{ width: `${Math.max(w, 2)}%` }}
                                    />
                                  </div>
                                  <span className="font-mono text-xs text-slate-700 tabular-nums w-16 text-right">{s.duration_ms}ms</span>
                                </div>
                              </td>
                            );
                          })}
                        </tr>
                      );
                    })
                  : /* 按 Type 分组对比 */
                    sortedTypeEntries
                      .map(([type, rows]: [string, Span[][]]) => {
                        const allDurations = rows.flat().map((s: Span) => s.duration_ms);
                        const maxMs = Math.max(...allDurations, 1);
                        return (
                          <tr key={type}>
                            <td className="py-2 px-4">
                              <div className="flex items-center gap-2">
                                <span className={`inline-block w-2.5 h-2.5 rounded ${spanTypeColor(type)}`} />
                                <span className="text-xs font-medium text-slate-700">{SPAN_TYPE_LABELS[type] || type}</span>
                                <span className="text-[9px] text-slate-400">{rows.flat().length} spans</span>
                              </div>
                            </td>
                            {rows.map((spans: Span[], i: number) => {
                              const totalMs = spans.reduce((s: number, x: Span) => s + x.duration_ms, 0);
                              const count = spans.length;
                              if (count === 0) return <td key={i} className="py-2 px-4 text-right text-xs text-slate-300">--</td>;
                              const w = (totalMs / maxMs) * 100;
                              return (
                                <td key={i} className="py-2 px-4">
                                  <div className="flex items-center gap-2 justify-end">
                                    <div className="w-16 h-2 bg-slate-100 rounded overflow-hidden">
                                      <div
                                        className={`h-full ${spanTypeColor(type)}`}
                                        style={{ width: `${Math.max(w, 2)}%` }}
                                      />
                                    </div>
                                    <span className="font-mono text-xs text-slate-700 tabular-nums w-16 text-right">{totalMs}ms</span>
                                    <span className="text-[9px] text-slate-400">×{count}</span>
                                  </div>
                                </td>
                              );
                            })}
                          </tr>
                        );
                      })
                }
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
