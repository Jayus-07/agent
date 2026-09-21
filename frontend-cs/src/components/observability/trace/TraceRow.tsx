"use client";

import { memo } from "react";
import {
  TraceRecord,
  statusBadge,
  durationColor,
  formatTime,
  formatRelative,
  formatCost,
} from "@/types/trace";

/**
 * Trace 列表行 — Langfuse 风格高密度布局。
 *
 * 设计（企业做法参考）：
 *   [状态] [主体双行: 问题预览 + ID·Session·KB·工作流小字] [耗时+相对比例条] [Usage: token+成本] [时间] [操作]
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
  const hasError = (t.error && Object.keys(t.error).length > 0) || t.status === "error";
  const stat = hasError ? "error"
    : t.status === "rejected" ? "rejected"
    : t.status === "timeout" || t.sla?.breached ? "timeout" : "success";
  const badge = statusBadge(stat);
  const slaBreached = t.sla?.breached;

  const spans = t.spans || [];
  const topSteps = [...spans].sort((a, b) => b.duration_ms - a.duration_ms).slice(0, 3);
  const err = hasError ? (t.error as Record<string, unknown>) : null;

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
      onClick={() => onNavigate(t.id)}
      className={`group cursor-pointer transition-colors hover:bg-slate-50 ${isCompared ? "ring-1 ring-violet-300 ring-inset" : ""}`}
    >
      {has("status") && (
        <td className="py-2.5 px-3 align-top">
          <div className="flex flex-col items-start gap-1">
            <span className={`inline-block px-1.5 py-0.5 rounded text-[9px] font-semibold ${badge.bg}`}>{badge.label}</span>
            {slaBreached && <span className="text-[9px] font-semibold text-red-500" title={`SLA ${t.sla?.threshold_ms}ms`}>SLA</span>}
          </div>
        </td>
      )}

      {has("question") && (
        <td className="py-2.5 px-3">
          {/* 行 1：问题预览 + 父子标记 + 错误码 */}
          <div className="flex items-center gap-1.5 min-w-0">
            {t.parent_id && (
              <span className="text-[9px] text-violet-500 bg-violet-50 rounded px-1 shrink-0" title={`子任务 · 父 ${t.parent_id.slice(0, 8)}`}>↳</span>
            )}
            {t.children_ids && t.children_ids.length > 0 && (
              <span className="text-[9px] text-blue-500 bg-blue-50 rounded px-1 shrink-0" title={`${t.children_ids.length} 个子任务`}>↑</span>
            )}
            <span className="text-[13px] font-medium text-slate-800 truncate" title={t.question}>
              {t.question || "--"}
            </span>
            {err && (
              <span className="text-[9px] font-mono text-red-600 bg-red-50 rounded px-1.5 py-0.5 shrink-0 cursor-help"
                title={String(err.message ?? "")}>
                {String(err.code ?? "ERROR")}
              </span>
            )}
          </div>
          {/* 行 2：ID · Session · KB · Workflow 元信息小字 */}
          <div className="flex items-center gap-2 mt-1 text-[10px] text-slate-400 font-mono min-w-0">
            <button
              onClick={(e) => { e.stopPropagation(); onCopy(t.id); }}
              className={`hover:text-slate-600 transition-opacity ${isCopied ? "text-emerald-500" : ""}`}
              title={isCopied ? "已复制" : "复制 Trace ID"}
            >
              {t.id.slice(0, 8)}
            </button>
            {isBookmarked && <span className="text-amber-400 not-italic">★</span>}
            <span className="text-slate-200">|</span>
            <button
              onClick={(e) => { e.stopPropagation(); onNavigate(`/observability/sessions/${t.session_id}`); }}
              className="hover:text-violet-600 truncate max-w-[140px]"
              title={`Session ${t.session_id}`}
            >
              {t.session_id.slice(0, 16)}
            </button>
            {String(t.metadata?.kb_id ?? "") && (
              <>
                <span className="text-slate-200">|</span>
                <span className="truncate max-w-[80px]">kb:{String(t.metadata?.kb_id)}</span>
              </>
            )}
            {t.workflow_name && (
              <>
                <span className="text-slate-200">|</span>
                <span>{t.workflow_name}</span>
              </>
            )}
          </div>
        </td>
      )}

      {has("duration") && (
        <td className="py-2.5 px-3 w-40">
          <div className="flex items-center justify-end gap-2">
            <span className={`font-mono tabular-nums text-xs font-semibold ${durationColor(t.duration_ms)}`}
              title={topSteps.map(s => `${s.name}: ${s.duration_ms}ms`).join("\n") || undefined}>
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
            {formatCost(t.cost_usd)}
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
          <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            <button
              onClick={(e) => { e.stopPropagation(); onToggleBookmark(t.id); }}
              className={`text-sm ${isBookmarked ? "text-amber-400" : "text-slate-300 hover:text-amber-400"}`}
              title={isBookmarked ? "取消收藏" : "收藏"}
            >
              {isBookmarked ? "★" : "☆"}
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); onToggleCompare(t.id); }}
              className={`text-xs px-1.5 py-0.5 rounded ${isCompared ? "bg-violet-100 text-violet-700" : "text-slate-400 hover:text-violet-600"}`}
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
