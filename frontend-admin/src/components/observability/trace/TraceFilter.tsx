"use client";

import { TraceFilter as FilterType, TraceRecord } from "@/types/trace";
import { useMemo } from "react";

interface Props {
  filter: FilterType;
  onChange: (f: FilterType) => void;
  traces: TraceRecord[];
}

const TIME_OPTIONS: { value: FilterType["timeRange"]; label: string }[] = [
  { value: "15m", label: "近15分钟" },
  { value: "1h", label: "近1小时" },
  { value: "6h", label: "近6小时" },
  { value: "24h", label: "近24小时" },
];

const STATUS_OPTIONS: { value: FilterType["status"]; label: string }[] = [
  { value: "all", label: "全部状态" },
  { value: "success", label: "成功" },
  { value: "error", label: "错误" },
  { value: "timeout", label: "超时" },
  { value: "cancelled", label: "取消" },
];

export default function TraceFilterBar({ filter, onChange, traces }: Props) {
  const kbOptions = useMemo(() => {
    const set = new Set<string>();
    traces.forEach((t) => {
      const kb = String(t.metadata?.kb_id ?? "");
      if (kb) set.add(kb);
    });
    return Array.from(set);
  }, [traces]);

  const modelOptions = useMemo(() => {
    const set = new Set<string>();
    traces.forEach((t) => t.model?.name && set.add(t.model.name));
    return Array.from(set);
  }, [traces]);

  return (
    <div className="flex flex-wrap items-center gap-3 px-1">
      {/* Time range */}
      <div className="flex rounded-lg border border-slate-200 overflow-hidden">
        {TIME_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            onClick={() => onChange({ ...filter, timeRange: opt.value })}
            className={`px-3 py-1.5 text-xs font-medium transition-colors ${
              filter.timeRange === opt.value
                ? "bg-violet-600 text-white"
                : "bg-white text-slate-600 hover:bg-slate-50"
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {/* Status */}
      <select
        value={filter.status}
        onChange={(e) => onChange({ ...filter, status: e.target.value as FilterType["status"] })}
        className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600 bg-white focus:outline-none focus:ring-2 focus:ring-violet-500/20"
      >
        {STATUS_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>{opt.label}</option>
        ))}
      </select>

      {/* KB */}
      <select
        value={filter.kb_id ?? ""}
        onChange={(e) => onChange({ ...filter, kb_id: e.target.value || undefined })}
        className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600 bg-white focus:outline-none focus:ring-2 focus:ring-violet-500/20"
      >
        <option value="">全部 KB</option>
        {kbOptions.map((kb) => (
          <option key={kb} value={kb}>{kb}</option>
        ))}
      </select>

      {/* Model */}
      <select
        value={filter.model ?? ""}
        onChange={(e) => onChange({ ...filter, model: e.target.value || undefined })}
        className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600 bg-white focus:outline-none focus:ring-2 focus:ring-violet-500/20"
      >
        <option value="">全部模型</option>
        {modelOptions.map((m) => (
          <option key={m} value={m}>{m}</option>
        ))}
      </select>

      {/* Keyword search */}
      <div className="relative flex-1 min-w-[200px]">
        <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" />
        </svg>
        <input
          type="text"
          placeholder="搜索 Prompt / 回复 / Trace ID / Session..."
          value={filter.keyword}
          onChange={(e) => onChange({ ...filter, keyword: e.target.value })}
          className="w-full rounded-lg border border-slate-200 pl-9 pr-3 py-1.5 text-xs text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-violet-500/20"
        />
      </div>
    </div>
  );
}