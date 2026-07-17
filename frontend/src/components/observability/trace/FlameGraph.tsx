"use client";

/**
 * 横向耗时条（火焰图总览）
 * 区别于 StepTimeline：用于详情页头部展示 span 耗时比例
 */

import { Span, spanColor } from "@/types/trace";

interface Props {
  steps: Span[];  // 兼容旧名
  totalMs: number;
  onStepClick?: (spanId: string) => void;
  highlightStepId?: string | null;
}

export default function FlameGraph({ steps, totalMs, onStepClick, highlightStepId }: Props) {
  // 仅展示有耗时的 span
  const visible = steps.filter((s) => s.duration_ms > 0);
  const ratioTotal = visible.reduce((s, x) => s + x.duration_ms, 0) || 1;

  return (
    <div className="space-y-2">
      {/* 火焰条 */}
      <div className="flex w-full h-8 rounded overflow-hidden bg-slate-100">
        {visible.map((s) => {
          const w = (s.duration_ms / ratioTotal) * 100;
          if (w < 0.3) return null;
          const isHL = highlightStepId === s.id;
          return (
            <button
              key={s.id}
              onClick={() => onStepClick?.(s.id)}
              title={`${s.name}: ${s.duration_ms}ms (${((s.duration_ms / totalMs) * 100).toFixed(1)}%)`}
              className={`relative h-full ${spanColor(s.status, s.duration_ms)} ${isHL ? "ring-2 ring-violet-400 z-10" : "hover:opacity-80"} transition-all`}
              style={{ width: `${w}%`, minWidth: "8px" }}
            >
              {w > 8 && (
                <span className="absolute inset-0 flex items-center justify-center text-[10px] font-mono text-white drop-shadow-sm truncate px-1">
                  {s.name}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* 时间轴 */}
      <div className="flex justify-between text-[10px] text-slate-400 font-mono">
        <span>0</span>
        <span>{(totalMs / 4).toFixed(0)}ms</span>
        <span>{(totalMs / 2).toFixed(0)}ms</span>
        <span>{(totalMs * 3 / 4).toFixed(0)}ms</span>
        <span>{totalMs}ms</span>
      </div>
    </div>
  );
}
