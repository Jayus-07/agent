"use client";

import { memo } from "react";
import {
  TraceRecord,
  durationColor,
  formatTime,
  formatRelative,
  formatCost,
  TRACE_SOURCE_LABELS,
} from "@/types/trace";
import { getRunStatus, getRunStatusLabel, getRunStatusTone, safeDiagnosticCode, safeRuntimeLabel } from "@/lib/observability/runtimeStatus";

/**
 * Trace 列表行 — Langfuse 风格高密度布局。
 *
 * 设计（企业做法参考）：
 *   列表只展示运行元数据；不输出用户请求、模型回复、Session ID 或原始错误信息。
 * 相对比例条相对全列表最大耗时归一化，扫一眼即可定位慢 trace；
 * hover 显示 top3 慢步骤（span 级拆解）。
 */

export type ColKey =
  | "status" | "question" | "duration" | "usage" | "time" | "actions";

export const VALID_COL_KEYS: ColKey[] = ["status", "question", "duration", "usage", "time", "actions"];

interface Props {
  t: TraceRecord;
  columns: ColKey[];
  maxDuration: number;
  isBookmarked: boolean;
  isCompared: boolean;
  isCopied: boolean;
  onNavigate: (target: string) => void;
  onCopy: (id: string) => void;
  onToggleBookmark: (id: string) => void;
  onToggleCompare: (id: string) => void;
}

function TraceRowInner({
  t, columns, maxDuration, isBookmarked, isCompared, isCopied,
  onNavigate, onCopy, onToggleBookmark, onToggleCompare,
}: Props) {
  const has = (k: ColKey) => columns.includes(k);
  const status = getRunStatus(t);
  const hasError = status === "error" || status === "failed";
  const slaBreached = t.sla?.breached;

  const spans = t.spans || [];
  const topSteps = [...spans].sort((a, b) => b.duration_ms - a.duration_ms).slice(0, 3);
  const errorCode = hasError ? safeDiagnosticCode(t.error?.code) : null;

  // 耗时相对比例条：相对全列表最大耗时（页面下传），线性宽度 + 状态色
  const ratio = maxDuration > 0
    ? Math.max(Math.min(t.duration_ms / maxDuration, 1), 0.02)
    : 0;
  const barColor = hasError ? "bg-red-400"
    : slaBreached ? "bg-red-300"
    : t.duration_ms > 5000 ? "bg-red-300"
    : t.duration_ms > 2000 ? "bg-amber-300"
    : "bg-emerald-300";

  const totalTokens = t.usage?.total_tokens ?? 0;

  return (
    <tr
      className={`group transition-colors hover:bg-slate-50 ${isCompared ? "ring-1 ring-violet-300 ring-inset" : ""}`}
    >
      {has("status") && (
        <td className="py-2.5 px-3 align-top">
          <div className="flex flex-col items-start gap-1">
            <span className={`inline-flex rounded border px-2 py-0.5 text-xs ${getRunStatusTone(status)}`}>{getRunStatusLabel(status)}</span>
            {slaBreached && <span className="text-[9px] font-semibold text-red-500" title={`SLA ${t.sla?.threshold_ms}ms`}>SLA</span>}
          </div>
        </td>
      )}

      {has("question") && (
        <td className="py-2.5 px-3">
          {/* 行 1：Trace ID + 安全状态码 */}
          <div className="flex items-center gap-1.5 min-w-0">
            {t.parent_id && (
              <span className="text-[9px] text-violet-500 bg-violet-50 rounded px-1 shrink-0" title={`子任务 · 父 ${t.parent_id.slice(0, 8)}`}>↳</span>
            )}
            {t.children_ids && t.children_ids.length > 0 && (
              <span className="text-[9px] text-blue-500 bg-blue-50 rounded px-1 shrink-0" title={`${t.children_ids.length} 个子任务`}>↑</span>
            )}
            <button
              type="button"
              onClick={() => onNavigate(t.id)}
              aria-label={`打开 Trace ${t.id}`}
              className="min-h-10 truncate text-left text-[13px] font-semibold text-slate-800 underline-offset-4 hover:text-accent hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              title={t.id}
            >
              {t.id.slice(0, 12)}
            </button>
            {errorCode && <span className="shrink-0 rounded bg-red-50 px-1.5 py-0.5 font-mono text-xs text-red-700">{errorCode}</span>}
          </div>
          {/* 行 2：Agent / Workflow 来源与关联数量；不显示问题文本或用户标识。 */}
          <div className="flex items-center gap-2 mt-1 text-[10px] text-slate-400 font-mono min-w-0">
            <button
              type="button"
              onClick={() => onCopy(t.id)}
              aria-label={isCopied ? "Trace ID 已复制" : `复制 Trace ID ${t.id}`}
              className={`min-h-10 rounded px-1 hover:text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${isCopied ? "text-emerald-600" : ""}`}
              title={isCopied ? "已复制" : "复制 Trace ID"}
            >
              {isCopied ? "已复制" : "复制 ID"}
            </button>
            {isBookmarked && <span className="text-amber-400 not-italic">★</span>}
            {/* 来源三分类（2026-10-08 #12）：后端 DTO source，显示名同口径映射 */}
            {t.source && (
              <>
                <span className="text-slate-200">|</span>
                <span className="font-medium text-violet-600">
                  {TRACE_SOURCE_LABELS[t.source] ?? safeRuntimeLabel(t.source, "来源未记录")}
                </span>
              </>
            )}
            <span className="truncate" title={safeRuntimeLabel(t.workflow_name, "工作流未记录")}>{safeRuntimeLabel(t.workflow_name, "工作流未记录")}</span>
            {(t.children_ids?.length || t.parent_id) && <span className="shrink-0">· 关联运行</span>}
          </div>
        </td>
      )}

      {has("duration") && (
        <td className="py-2.5 px-3 w-40">
          <div className="flex items-center justify-end gap-2">
            <span className={`font-mono tabular-nums text-xs font-semibold ${durationColor(t.duration_ms)}`}
              title={topSteps.map(s => `${safeRuntimeLabel(s.name, "执行步骤")}: ${s.duration_ms}ms`).join("\n") || undefined}>
              {t.duration_ms >= 1000 ? `${(t.duration_ms / 1000).toFixed(1)}s` : `${t.duration_ms}ms`}
            </span>
            {/* 相对比例条 */}
            <div className="w-16 h-1.5 bg-slate-100 rounded-full overflow-hidden shrink-0">
              <div className={`h-full rounded-full ${barColor}`} style={{ width: `${ratio * 100}%` }} />
            </div>
          </div>
        </td>
      )}

      {has("usage") && (
        <td className="py-2.5 px-3 text-right align-top">
          <div className="font-mono tabular-nums text-xs text-slate-600">
            {totalTokens > 0 ? totalTokens.toLocaleString() : <span className="text-slate-300">--</span>}
            <span className="text-slate-300 text-[9px]"> tok</span>
          </div>
          <div className="font-mono tabular-nums text-[10px] text-emerald-600 mt-0.5">
            {typeof t.cost_cny === "number" ? formatCost(t.cost_cny) : <span className="text-slate-400">未采集费用</span>}
          </div>
        </td>
      )}

      {has("time") && (
        <td className="py-2.5 px-3 align-top">
          <div className="text-[11px] text-slate-600">{formatRelative(t.timestamp)}</div>
          <div className="text-[9px] text-slate-400 font-mono" title={formatTime(t.timestamp)}>
            {formatTime(t.timestamp)}
          </div>
        </td>
      )}

      {has("actions") && (
        <td className="py-2.5 px-3 align-top">
          <div className="flex items-center gap-1 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100 motion-reduce:transition-none">
            <button
              type="button"
              onClick={() => onToggleBookmark(t.id)}
              aria-label={isBookmarked ? "取消收藏" : "收藏 Trace"}
              className={`min-h-10 min-w-10 rounded text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${isBookmarked ? "text-amber-500" : "text-slate-400 hover:text-amber-500"}`}
              title={isBookmarked ? "取消收藏" : "收藏"}
            >
              {isBookmarked ? "★" : "☆"}
            </button>
            <button
              type="button"
              onClick={() => onToggleCompare(t.id)}
              aria-label={isCompared ? "从对比中移除" : "加入 Trace 对比"}
              className={`min-h-10 rounded px-2 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${isCompared ? "bg-violet-100 text-violet-700" : "text-slate-400 hover:text-violet-600"}`}
              title="加入对比"
            >
              ⚖
            </button>
          </div>
        </td>
      )}
    </tr>
  );
}

const TraceRow = memo(TraceRowInner);
export default TraceRow;
