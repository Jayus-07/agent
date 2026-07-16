"use client";

import { useMemo } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import mockTraces from "@/mock/traces.json";
import TraceBreadcrumb from "@/components/observability/trace/TraceBreadcrumb";
import {
  TraceRecord,
  durationColor,
  formatTime,
  formatBoth,
  formatCost,
  statusBadge,
} from "@/types/trace";

const typedTraces = mockTraces as unknown as TraceRecord[];

export default function SessionDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();

  const sessionTraces = useMemo(
    () => typedTraces.filter((t) => t.session_id === id).sort((a, b) => a.timestamp.localeCompare(b.timestamp)),
    [id]
  );

  const summary = useMemo(() => {
    if (sessionTraces.length === 0) return null;
    const total = sessionTraces.reduce((s, t) => s + t.duration_ms, 0);
    const totalTokens = sessionTraces.reduce((s, t) => s + (t.usage?.total_tokens ?? 0), 0);
    const totalCost = sessionTraces.reduce((s, t) => s + (t.cost_usd ?? 0), 0);
    const errors = sessionTraces.filter((t) => t.error && Object.keys(t.error).length > 0).length;
    const slaBreached = sessionTraces.filter((t) => t.sla?.breached).length;
    const first = sessionTraces[0];
    const userName = String(first.session?.user_name ?? first.metadata?.user_name ?? "--");
    const userId = String(first.session?.user_id ?? first.metadata?.user_id ?? "--");

    return {
      total,
      totalTokens,
      totalCost,
      errors,
      slaBreached,
      userName,
      userId,
      startedAt: first.session?.started_at ?? first.timestamp,
    };
  }, [sessionTraces]);

  if (!summary) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="text-center">
          <p className="text-slate-400 text-lg font-mono">Session {id} 不存在</p>
          <button onClick={() => router.push("/observability/traces")} className="mt-3 text-sm text-violet-600 hover:text-violet-500">← 返回列表</button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="max-w-[1440px] mx-auto px-6 py-6 space-y-5">
        <TraceBreadcrumb crumbs={[
          { label: "可观测中心", href: "/observability" },
          { label: "链路追踪", href: "/observability/traces" },
          { label: `Session ${id}` },
        ]} />

        {/* Header */}
        <div className="bg-white border border-slate-200 rounded-xl p-5">
          <div className="flex items-center justify-between flex-wrap gap-3">
            <div>
              <h1 className="text-lg font-semibold text-slate-800">Session 详情</h1>
              <p className="text-xs text-slate-500 mt-1 font-mono">{id}</p>
            </div>
            <Link
              href={`/observability/traces?session=${id}`}
              className="text-xs text-violet-600 hover:text-violet-800"
            >
              在链路追踪中查看 →
            </Link>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-6 gap-4 mt-5">
            <Stat label="用户" value={summary.userName} sub={summary.userId} />
            <Stat label="Trace 数" value={String(sessionTraces.length)} />
            <Stat label="总耗时" value={`${(summary.total / 1000).toFixed(2)}s`} />
            <Stat label="总 Token" value={String(summary.totalTokens)} />
            <Stat label="总成本" value={formatCost(summary.totalCost)} color="text-emerald-600" />
            <Stat label="错误 / SLA" value={`${summary.errors} / ${summary.slaBreached}`} color={summary.errors + summary.slaBreached > 0 ? "text-red-600" : "text-emerald-600"} />
          </div>

          <div className="mt-4 pt-4 border-t border-slate-100 text-xs text-slate-500">
            开始于 <span className="font-mono">{formatBoth(summary.startedAt)}</span>
          </div>
        </div>

        {/* Timeline */}
        <section>
          <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">⏱ 调用时间线</h2>
          <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs font-medium text-slate-500">
                  <th className="py-3 px-4 w-10">#</th>
                  <th className="py-3 px-4 w-10">状态</th>
                  <th className="py-3 px-4">用户问题</th>
                  <th className="py-3 px-4 w-24 text-right">耗时</th>
                  <th className="py-3 px-4 w-24 text-right">成本</th>
                  <th className="py-3 px-4 w-32">时间</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {sessionTraces.map((t, idx) => {
                  const hasErr = t.error && Object.keys(t.error).length > 0;
                  const stat = hasErr || t.status === "error" ? "error" : t.status === "timeout" || t.duration_ms > 5000 ? "timeout" : "success";
                  const badge = statusBadge(stat);
                  return (
                    <tr
                      key={t.id}
                      onClick={() => router.push(`/observability/traces/${t.id}`)}
                      className="cursor-pointer hover:bg-slate-50 transition-colors"
                    >
                      <td className="py-3 px-4 text-xs text-slate-400 font-mono">{idx + 1}</td>
                      <td className="py-3 px-4">
                        <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold ${badge.bg}`}>{badge.label}</span>
                      </td>
                      <td className="py-3 px-4">
                        <div className="flex items-center gap-2">
                          {t.parent_id && <span className="text-[9px] text-violet-500 bg-violet-50 rounded px-1">↳</span>}
                          <span className="text-slate-700 line-clamp-1">{t.question}</span>
                        </div>
                      </td>
                      <td className="py-3 px-4 text-right">
                        <span className={`font-mono tabular-nums text-xs font-semibold ${durationColor(t.duration_ms)}`}>{t.duration_ms}ms</span>
                      </td>
                      <td className="py-3 px-4 text-right">
                        <span className="font-mono tabular-nums text-xs text-emerald-600">{formatCost(t.cost_usd)}</span>
                      </td>
                      <td className="py-3 px-4 text-[10px] text-slate-400 font-mono">{formatTime(t.timestamp)}</td>
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

function Stat({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-widest text-slate-400 mb-1">{label}</p>
      <p className={`font-mono text-sm font-semibold ${color ?? "text-slate-800"}`}>{value}</p>
      {sub && <p className="text-[10px] text-slate-400 mt-0.5 font-mono">{sub}</p>}
    </div>
  );
}