"use client";

import { TraceStats } from "@/types/trace";
import { formatCost } from "@/types/trace";

interface Props {
  stats: TraceStats;
  traceCount: number;
  /** 当前筛选内 SLA 违反数（前端算：sla.breached） */
  breachedCount?: number;
  /** KPI 卡点击 → 联动列表筛选（企业做法：点错误卡直接看失败请求） */
  onSelectStatus?: (status: "all" | "error") => void;
  activeStatus?: string;
}

export default function StatsBar({ stats, traceCount, breachedCount = 0, onSelectStatus, activeStatus }: Props) {
  const clickable = typeof onSelectStatus === "function";
  const cards: Array<{
    label: string;
    value: string;
    sub?: string;
    color?: string;
    onClick?: () => void;
    active?: boolean;
  }> = [
    {
      label: "Traces (24h)",
      value: String(stats.total_24h),
      sub: `当前筛选 ${traceCount} 条`,
      onClick: clickable ? () => onSelectStatus("all") : undefined,
      active: clickable && activeStatus === "all",
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
      onClick: clickable && stats.error_count >= 0 ? () => onSelectStatus("error") : undefined,
      active: clickable && activeStatus === "error",
    },
    {
      label: "SLA 违反",
      value: String(breachedCount),
      color: breachedCount > 0 ? "text-red-500" : "text-emerald-600",
      sub: "当前筛选内",
    },
    {
      label: "总成本",
      value: formatCost(stats.total_cost_usd ?? 0),
      color: "text-emerald-600",
      sub: "时间窗内",
    },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
      {cards.map((c) => {
        const clickableCard = typeof c.onClick === "function";
        return (
          <div
            key={c.label}
            onClick={c.onClick}
            className={`bg-white border rounded-xl px-4 py-3 transition-colors ${
              c.active
                ? "border-violet-400 ring-1 ring-violet-300"
                : "border-slate-200"
            } ${clickableCard ? "cursor-pointer hover:border-violet-300 hover:shadow-sm" : ""}`}
          >
            <p className="text-[10px] uppercase tracking-widest text-slate-400 mb-1">{c.label}</p>
            <p className={`text-lg font-bold font-mono tabular-nums ${c.color ?? "text-slate-800"}`}>{c.value}</p>
            {c.sub && <p className="text-[10px] text-slate-400 mt-0.5">{c.sub}</p>}
          </div>
        );
      })}
    </div>
  );
}
