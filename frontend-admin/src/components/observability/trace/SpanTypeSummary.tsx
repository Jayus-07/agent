"use client";

import { Span, spanTypeColor, SPAN_TYPE_LABELS } from "@/types/trace";

/**
 * Span 耗时占比摘要 —— 按 type 聚合，一眼看出哪个类型吃掉了时间
 * 例如：LLM 71% | Retriever 12% | HTTP 9% | Tool 6% | Other 2%
 */

interface Props {
  spans: Span[];
  totalMs: number;
  onTypeClick?: (type: string) => void;
}

export default function SpanTypeSummary({ spans, totalMs, onTypeClick }: Props) {
  // 按 type 聚合
  const agg = new Map<string, { ms: number; count: number }>();
  for (const s of spans) {
    if (s.duration_ms <= 0) continue;
    const entry = agg.get(s.type) || { ms: 0, count: 0 };
    entry.ms += s.duration_ms;
    entry.count += 1;
    agg.set(s.type, entry);
  }

  // 排序：耗时多→少
  const sorted = Array.from(agg.entries()).sort((a, b) => b[1].ms - a[1].ms);
  if (sorted.length === 0) return null;

  const ratioTotal = sorted.reduce((s, [, v]) => s + v.ms, 0) || 1;

  return (
    <div className="space-y-2">
      {/* 汇总条 */}
      <div className="flex w-full h-7 rounded-md overflow-hidden bg-slate-100">
        {sorted.map(([type, { ms }]) => {
          const pct = (ms / ratioTotal) * 100;
          if (pct < 0.5) return null;
          return (
            <button
              key={type}
              onClick={() => onTypeClick?.(type)}
              title={`${SPAN_TYPE_LABELS[type] || type}: ${ms}ms (${pct.toFixed(1)}%)`}
              className={`h-full ${spanTypeColor(type)} hover:opacity-80 transition-opacity`}
              style={{ width: `${pct}%`, minWidth: "4px" }}
            >
              {pct > 10 && (
                <span className="flex items-center justify-center h-full text-[10px] font-semibold text-white drop-shadow-sm truncate px-1">
                  {pct.toFixed(0)}%
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* 图例 */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        {sorted.map(([type, { ms, count }]) => {
          const pct = ((ms / ratioTotal) * 100).toFixed(1);
          const label = SPAN_TYPE_LABELS[type] || type;
          return (
            <button
              key={type}
              onClick={() => onTypeClick?.(type)}
              className="flex items-center gap-1.5 text-[10px] text-slate-600 hover:text-slate-800 transition-colors"
            >
              <span className={`inline-block w-2.5 h-2.5 rounded-sm ${spanTypeColor(type)}`} />
              <span className="font-medium">{label}</span>
              <span className="text-slate-400">{pct}%</span>
              <span className="text-slate-400">· {ms}ms</span>
              <span className="text-slate-300">({count})</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
