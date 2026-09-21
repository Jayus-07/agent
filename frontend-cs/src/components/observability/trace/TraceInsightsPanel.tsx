"use client";

import { useMemo } from "react";
import { TraceRecord } from "@/types/trace";

/**
 * TraceInsightsPanel — 列表页洞察区（企业 trace 平台标配）
 *
 * 左：延迟分布直方图（4 桶，点击桶过滤下方列表）
 * 右：按小时请求量趋势（错误标红），快速定位事故时段
 * 数据基于当前筛选结果（除桶过滤外），保证选桶后其他桶仍可见。
 */

export type DurationBucket = "fast" | "normal" | "slow" | "very_slow" | "";

export const DURATION_BUCKETS: { key: DurationBucket; label: string; min: number; max: number; color: string }[] = [
  { key: "fast", label: "<2s", min: 0, max: 2000, color: "bg-emerald-400" },
  { key: "normal", label: "2-5s", min: 2000, max: 5000, color: "bg-lime-400" },
  { key: "slow", label: "5-15s", min: 5000, max: 15000, color: "bg-amber-400" },
  { key: "very_slow", label: ">15s", min: 15000, max: Infinity, color: "bg-red-400" },
];

export function bucketOf(ms: number): DurationBucket {
  return DURATION_BUCKETS.find((b) => ms >= b.min && ms < b.max)?.key ?? "very_slow";
}

interface Props {
  traces: TraceRecord[];
  activeBucket: DurationBucket;
  onBucketClick: (b: DurationBucket) => void;
}

export default function TraceInsightsPanel({ traces, activeBucket, onBucketClick }: Props) {
  const buckets = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const b of DURATION_BUCKETS) counts[b.key] = 0;
    for (const t of traces) counts[bucketOf(t.duration_ms || 0)]++;
    const max = Math.max(...Object.values(counts), 1);
    return { counts, max };
  }, [traces]);

  const hourly = useMemo(() => {
    // 最近 24h 按小时分桶：请求量 + 错误数
    const now = Date.now();
    const slots: { hourLabel: string; total: number; errors: number }[] = [];
    for (let i = 23; i >= 0; i--) {
      const d = new Date(now - i * 3600_000);
      slots.push({ hourLabel: `${String(d.getHours()).padStart(2, "0")}`, total: 0, errors: 0 });
    }
    for (const t of traces) {
      const ts = Date.parse(t.timestamp || "");
      if (Number.isNaN(ts) || now - ts > 24 * 3600_000) continue;
      const idx = 23 - Math.floor((now - ts) / 3600_000);
      if (idx < 0 || idx > 23) continue;
      slots[idx].total++;
      const hasError = (t.error && Object.keys(t.error).length > 0) || t.status === "error";
      if (hasError) slots[idx].errors++;
    }
    const maxTotal = Math.max(...slots.map((s) => s.total), 1);
    return { slots, maxTotal };
  }, [traces]);

  return (
    <div className="bg-white border border-slate-200 rounded-xl p-4 flex flex-col lg:flex-row gap-6">
      {/* 延迟分布（可点击过滤） */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-2">
          <p className="text-[10px] uppercase tracking-widest text-slate-400">延迟分布</p>
          {activeBucket && (
            <button
              onClick={() => onBucketClick("")}
              className="text-[10px] text-violet-600 hover:text-violet-700"
            >
              清除过滤
            </button>
          )}
        </div>
        <div className="space-y-1.5">
          {DURATION_BUCKETS.map((b) => {
            const n = buckets.counts[b.key] ?? 0;
            const active = activeBucket === b.key;
            return (
              <button
                key={b.key}
                onClick={() => onBucketClick(active ? "" : b.key)}
                className={`w-full flex items-center gap-2 group ${active ? "" : "opacity-90 hover:opacity-100"}`}
              >
                <span className={`w-12 shrink-0 text-right text-[10px] font-mono ${active ? "text-violet-700 font-bold" : "text-slate-500"}`}>
                  {b.label}
                </span>
                <span className="flex-1 h-4 bg-slate-100 rounded-sm overflow-hidden">
                  <span
                    className={`block h-full rounded-sm ${b.color} ${active ? "ring-2 ring-violet-400" : "group-hover:opacity-80"} transition-all`}
                    style={{ width: `${Math.max((n / buckets.max) * 100, n > 0 ? 2 : 0)}%` }}
                  />
                </span>
                <span className={`w-10 shrink-0 text-[10px] font-mono tabular-nums ${active ? "text-violet-700 font-bold" : "text-slate-500"}`}>
                  {n}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* 24h 趋势 */}
      <div className="flex-1 min-w-0 border-t lg:border-t-0 lg:border-l border-slate-100 pt-3 lg:pt-0 lg:pl-6">
        <p className="text-[10px] uppercase tracking-widest text-slate-400 mb-2">
          请求趋势 (24h) · <span className="text-red-400">红 = 错误</span>
        </p>
        <div className="flex items-end gap-[3px] h-14">
          {hourly.slots.map((s, i) => (
            <div key={i} className="flex-1 flex flex-col justify-end items-center gap-0.5 group relative">
              {/* tooltip */}
              <span className="absolute -top-6 hidden group-hover:block text-[9px] font-mono bg-slate-800 text-white rounded px-1.5 py-0.5 whitespace-nowrap z-10">
                {s.hourLabel}:00 · {s.total} 条{s.errors ? ` · ${s.errors} 错误` : ""}
              </span>
              {s.errors > 0 && (
                <span
                  className="w-full rounded-t-sm bg-red-400"
                  style={{ height: `${Math.max((s.errors / hourly.maxTotal) * 56, 2)}px` }}
                />
              )}
              <span
                className={`w-full rounded-t-sm ${s.total > 0 ? "bg-violet-300 group-hover:bg-violet-400" : "bg-slate-100"} transition-colors`}
                style={{ height: `${Math.max(((s.total - s.errors) / hourly.maxTotal) * 56, s.total > 0 ? 2 : 1)}px` }}
              />
            </div>
          ))}
        </div>
        <div className="flex justify-between text-[9px] text-slate-400 font-mono mt-1">
          <span>{hourly.slots[0]?.hourLabel}:00</span>
          <span>现在</span>
        </div>
      </div>
    </div>
  );
}
