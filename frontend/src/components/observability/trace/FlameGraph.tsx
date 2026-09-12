"use client";

/**
 * Span 时间轴甘特图（waterfall）
 *
 * Why 重写：旧实现把所有 span 按"耗时之和"平铺堆叠成一条总览条，
 * 嵌套 span 加总超过 trace 总耗时（与下方时间轴对不上），且无法回答
 * "时间去哪了"——哪个阶段串行等待、哪段无埋点黑洞。新实现按
 * start_time/end_time 绝对时间轴定位每行，真实还原执行时序。
 */

import { useMemo } from "react";
import { Span, spanColor } from "@/types/trace";

interface Props {
  steps: Span[];  // 兼容旧名
  totalMs: number;
  onStepClick?: (spanId: string) => void;
  highlightStepId?: string | null;
}

/** 解析 ISO 时间；失败返回 null（合成 span 无 start_time 时由调用方回退） */
function toTs(iso: string | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t;
}

export default function FlameGraph({ steps, totalMs, onStepClick, highlightStepId }: Props) {
  const rows = useMemo(() => {
    const timed = steps
      .map((s) => ({ span: s, start: toTs(s.start_time), end: toTs(s.end_time) }))
      .filter((r): r is { span: Span; start: number; end: number } =>
        r.start !== null && r.end !== null && r.end > r.start);
    if (timed.length === 0) return null;

    const t0 = Math.min(...timed.map((r) => r.start));
    const t1 = Math.max(...timed.map((r) => r.end));
    const span = Math.max(t1 - t0, 1);

    // 深度：沿 parent 链计算（仅用于缩进展示）
    const byId = new Map(timed.map((r) => [r.span.id, r.span]));
    const depthOf = (s: Span): number => {
      let d = 0;
      let cur: Span | undefined = s;
      const seen = new Set<string>();
      while (cur?.parent_id && byId.has(cur.parent_id) && !seen.has(cur.id)) {
        seen.add(cur.id);
        cur = byId.get(cur.parent_id);
        d++;
      }
      return d;
    };

    return timed
      .sort((a, b) => a.start - b.start)
      .map((r) => ({
        span: r.span,
        depth: depthOf(r.span),
        leftPct: ((r.start - t0) / span) * 100,
        widthPct: ((r.end - r.start) / span) * 100,
      }));
  }, [steps]);

  // 无有效时间数据（旧 trace / 合成 span）→ 回退为占比条
  if (!rows) {
    const visible = steps.filter((s) => s.duration_ms > 0);
    const ratioTotal = visible.reduce((s, x) => s + x.duration_ms, 0) || 1;
    return (
      <div className="space-y-2">
        <div className="flex w-full h-8 rounded overflow-hidden bg-slate-100">
          {visible.map((s) => {
            const w = (s.duration_ms / ratioTotal) * 100;
            if (w < 0.3) return null;
            const isHL = highlightStepId === s.id;
            return (
              <button
                key={s.id}
                onClick={() => onStepClick?.(s.id)}
                title={`${s.name}: ${s.duration_ms}ms`}
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
        <p className="text-[10px] text-slate-400">该 trace 无精确时间戳，退化为占比视图</p>
      </div>
    );
  }

  return (
    <div className="space-y-0.5">
      {rows.map(({ span: s, depth, leftPct, widthPct }) => {
        const isHL = highlightStepId === s.id;
        const w = Math.max(widthPct, 0.4);
        const left = Math.min(leftPct, 99.6);
        const showLabel = w > 14;
        return (
          <button
            key={s.id}
            onClick={() => onStepClick?.(s.id)}
            title={`${s.name}: ${s.duration_ms}ms · 起点 +${((leftPct / 100) * totalMs).toFixed(0)}ms`}
            className={`group flex items-center w-full h-5 rounded hover:bg-slate-50 ${isHL ? "bg-violet-50 ring-1 ring-violet-300" : ""}`}
          >
            <span
              className="w-36 shrink-0 truncate text-left text-[10px] text-slate-500"
              style={{ paddingLeft: `${Math.min(depth * 10, 80) + 4}px` }}
            >
              {s.name}
            </span>
            <span className="relative flex-1 h-full">
              <span
                className={`absolute inset-y-0.5 rounded-sm ${spanColor(s.status, s.duration_ms)} ${isHL ? "ring-2 ring-violet-400 z-10" : "group-hover:opacity-80"} transition-all`}
                style={{ left: `${left}%`, width: `${Math.min(w, 100 - left)}%`, minWidth: "3px" }}
              />
              {showLabel ? (
                <span
                  className="absolute inset-y-0 flex items-center text-[9px] font-mono text-white pl-1 drop-shadow-sm"
                  style={{ left: `${left}%` }}
                >
                  {s.duration_ms}ms
                </span>
              ) : (
                s.duration_ms > 500 && (
                  <span
                    className="absolute inset-y-0 flex items-center text-[9px] font-mono text-slate-400 whitespace-nowrap"
                    style={{ left: `calc(${Math.min(left + w, 99.6)}% + 4px)` }}
                  >
                    {s.duration_ms}ms
                  </span>
                )
              )}
            </span>
          </button>
        );
      })}

      {/* 时间轴刻度（pl-36 与行名称栏对齐） */}
      <div className="flex justify-between text-[10px] text-slate-400 font-mono pt-1 border-t border-slate-100 pl-36">
        <span>0</span>
        <span>{(totalMs / 4).toFixed(0)}ms</span>
        <span>{(totalMs / 2).toFixed(0)}ms</span>
        <span>{(totalMs * 3 / 4).toFixed(0)}ms</span>
        <span>{totalMs}ms</span>
      </div>
    </div>
  );
}
