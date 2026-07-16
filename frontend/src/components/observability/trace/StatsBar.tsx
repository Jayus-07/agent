"use client";

import { TraceStats } from "@/types/trace";
import Sparkline from "./Sparkline";
import { formatCost } from "@/types/trace";

interface Props {
  stats: TraceStats;
  traceCount: number;
}

export default function StatsBar({ stats, traceCount }: Props) {
  const cards = [
    {
      label: "Traces (24h)",
      value: String(stats.total_24h),
      sub: `当前筛选 ${traceCount} 条`,
    },
    {
      label: "成功率",
      value: (stats.success_rate * 100).toFixed(1) + "%",
      color: stats.success_rate >= 0.95 ? "text-emerald-600" : "text-amber-600",
    },
    {
      label: "平均耗时",
      value: (stats.avg_duration_ms / 1000).toFixed(2) + "s",
    },
    {
      label: "P95 耗时",
      value: (stats.p95_duration_ms / 1000).toFixed(2) + "s",
      color: stats.p95_duration_ms > 5000 ? "text-red-500" : "",
    },
    {
      label: "错误数",
      value: String(stats.error_count),
      color: stats.error_count > 0 ? "text-red-500" : "text-emerald-600",
    },
    {
      label: "总成本",
      value: formatCost(stats.total_cost_usd ?? 0),
      color: "text-emerald-600",
      sub: "近 24h",
    },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
      {cards.map((c) => (
        <div key={c.label} className="bg-white border border-slate-200 rounded-xl px-4 py-3">
          <p className="text-[10px] uppercase tracking-widest text-slate-400 mb-1">{c.label}</p>
          <p className={`text-lg font-bold font-mono tabular-nums ${c.color ?? "text-slate-800"}`}>{c.value}</p>
          {c.sub && <p className="text-[10px] text-slate-400 mt-0.5">{c.sub}</p>}
        </div>
      ))}
    </div>
  );
}