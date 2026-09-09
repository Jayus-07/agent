"use client";

import { memo } from "react";
import {
  TraceRecord,
  statusBadge,
  durationColor,
  durationBg,
  formatTime,
  formatRelative,
  truncate,
  formatCost,
} from "@/types/trace";

/**
 * Trace 列表行 — React.memo 隔离重渲染。
 *
 * 背景：原实现内联在页面 map 中，copiedId/bookmarks 等任一状态变化
 * 都会重渲全部行。抽出后仅在自身 props 变化时重渲（配合页面侧
 * useCallback 稳定回调）。
 */

export type ColKey =
  | "status" | "id" | "question" | "duration" | "tokens"
  | "cost" | "session" | "kb" | "time" | "actions";

interface Props {
  t: TraceRecord;
  columns: ColKey[];
  isBookmarked: boolean;
  isCompared: boolean;
  isCopied: boolean;
  onNavigate: (id: string) => void;
  onCopy: (id: string) => void;
  onToggleBookmark: (id: string) => void;
  onToggleCompare: (id: string) => void;
}

function TraceRowInner({
  t, columns, isBookmarked, isCompared, isCopied,
  onNavigate, onCopy, onToggleBookmark, onToggleCompare,
}: Props) {
  const has = (k: ColKey) => columns.includes(k);
  const stat = (t.error && Object.keys(t.error).length > 0) || t.status === "error" ? "error"
    : t.status === "timeout" || t.sla?.breached ? "timeout" : "success";
  const badge = statusBadge(stat);
  const spans = t.spans || [];
  const topSteps = [...spans].sort((a, b) => b.duration_ms - a.duration_ms).slice(0, 3);
  const slaBreached = t.sla?.breached;

  return (
    <tr
      onClick={() => onNavigate(t.id)}
      className={`group cursor-pointer transition-colors hover:bg-slate-50 ${durationBg(t.duration_ms)} ${isCompared ? "ring-1 ring-violet-300 ring-inset" : ""}`}
    >
      {has("status") && (
        <td className="py-3 px-4">
          <div className="flex items-center gap-1">
            <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold ${badge.bg}`}>{badge.label}</span>
            {slaBreached && <span className="text-[9px] text-red-500" title="SLA 违反">SLA</span>}
          </div>
        </td>
      )}
      {has("id") && (
        <td className="py-3 px-4">
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs text-slate-600">{t.id.slice(0, 8)}...</span>
            {isBookmarked && <span className="text-amber-400 text-xs">★</span>}
            <button onClick={(e) => { e.stopPropagation(); onCopy(t.id); }} className="opacity-0 group-hover:opacity-100 text-slate-400 hover:text-slate-600 transition-opacity" title="复制">
              {isCopied ? <span className="text-[10px] text-emerald-500">已复制</span> : <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" /></svg>}
            </button>
          </div>
        </td>
      )}
      {has("question") && (
        <td className="py-3 px-4">
          <div className="flex items-center gap-2">
            {t.parent_id && (
              <span className="text-[9px] text-violet-500 bg-violet-50 rounded px-1" title={`子任务 · 父 ${t.parent_id.slice(0, 8)}`}>↳ 子</span>
            )}
            {t.children_ids && t.children_ids.length > 0 && (
              <span className="text-[9px] text-blue-500 bg-blue-50 rounded px-1" title={`${t.children_ids.length} 个子任务`}>↑ 父</span>
            )}
            <span className="text-slate-700 line-clamp-1" title={t.question}>{truncate(t.question, 50)}</span>
          </div>
        </td>
      )}
      {has("duration") && (
        <td className="py-3 px-4 text-right">
          <span className={`font-mono tabular-nums font-semibold ${durationColor(t.duration_ms)}`} title={topSteps.map(s => `${s.name}: ${s.duration_ms}ms`).join("\n")}>
            {t.duration_ms}ms
          </span>
        </td>
      )}
      {has("tokens") && (
        <td className="py-3 px-4">
          <div className="flex items-center gap-1 text-xs font-mono tabular-nums">
            <span className="text-slate-400">{t.usage?.prompt_tokens ?? 0}</span>
            <span className="text-slate-300">/</span>
            <span className="text-slate-600 font-medium">{t.usage?.completion_tokens ?? 0}</span>
          </div>
        </td>
      )}
      {has("cost") && (
        <td className="py-3 px-4 text-right">
          <span className="font-mono tabular-nums text-xs text-emerald-600">{formatCost(t.cost_usd)}</span>
        </td>
      )}
      {has("session") && (
        <td className="py-3 px-4">
          <button
            onClick={(e) => { e.stopPropagation(); onNavigate(`/observability/sessions/${t.session_id}`); }}
            className="text-xs font-mono text-slate-500 hover:text-violet-600 bg-slate-100 hover:bg-violet-50 rounded px-1.5 py-0.5 transition-colors"
          >
            {t.session_id.slice(0, 16)}
          </button>
        </td>
      )}
      {has("kb") && (
        <td className="py-3 px-4">
          <span className="text-[10px] text-slate-500 bg-slate-100 rounded px-1.5 py-0.5 font-mono">
            {String(t.metadata?.kb_id ?? "--")}
          </span>
        </td>
      )}
      {has("time") && (
        <td className="py-3 px-4">
          <div className="text-[10px] text-slate-500" title={formatTime(t.timestamp)}>
            <div>{formatRelative(t.timestamp)}</div>
            <div className="font-mono text-slate-400">{formatTime(t.timestamp)}</div>
          </div>
        </td>
      )}
      {has("actions") && (
        <td className="py-3 px-4">
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
