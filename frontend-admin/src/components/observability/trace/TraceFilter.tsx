"use client";

import { TraceFilter as FilterType, TraceRecord, TRACE_SOURCE_LABELS } from "@/types/trace";
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
  { value: "failed", label: "业务失败" },
  { value: "error", label: "执行错误" },
  { value: "degraded", label: "降级" },
  { value: "partial", label: "部分成功" },
  { value: "timeout", label: "超时" },
  { value: "cancelled", label: "取消" },
  { value: "rejected", label: "拒绝" },
  { value: "running", label: "运行中" },
  { value: "unknown", label: "状态未知" },
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

  const workflowOptions = useMemo(() => {
    const names = new Set<string>();
    traces.forEach((trace) => {
      if (trace.workflow_name) names.add(trace.workflow_name);
    });
    return Array.from(names).sort((a, b) => a.localeCompare(b));
  }, [traces]);

  return (
    <div className="flex flex-wrap items-center gap-3 px-1">
      {/* Time range */}
      <div className="flex rounded-lg border border-slate-200 overflow-hidden">
        {TIME_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            type="button"
            aria-pressed={filter.timeRange === opt.value}
            aria-label={`按时间筛选：${opt.label}`}
            onClick={() => onChange({ ...filter, timeRange: opt.value })}
            className={`px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent ${
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
        aria-label="运行状态"
        value={filter.status}
        onChange={(e) => onChange({ ...filter, status: e.target.value as FilterType["status"] })}
        className="min-h-10 rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600 bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        {STATUS_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>{opt.label}</option>
        ))}
      </select>

      {/* workflow_name 有真实服务端过滤；execution_type 未出现在 Trace DTO，明确标为 BLOCKED。 */}
      <select
        aria-label="Agent 或 Workflow"
        value={filter.workflow_name ?? ""}
        onChange={(e) => onChange({ ...filter, workflow_name: e.target.value || undefined })}
        className="min-h-10 rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-600 bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <option value="">全部 Agent / Workflow</option>
        {workflowOptions.map((workflow) => (
          <option key={workflow} value={workflow}>{workflow === "agent" ? "Agent" : workflow}</option>
        ))}
      </select>

      <label className="flex min-h-10 items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 text-xs text-amber-900" title="列表 DTO 未提供 execution_type 字段">
        <span>执行类型</span>
        <span className="rounded border border-amber-300 px-1.5 py-0.5 font-mono text-[10px]">BLOCKED</span>
      </label>

      {/* KB */}
      <select
        aria-label="知识库"
        value={filter.kb_id ?? ""}
        onChange={(e) => onChange({ ...filter, kb_id: e.target.value || undefined })}
        className="min-h-10 rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600 bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <option value="">全部 KB</option>
        {kbOptions.map((kb) => (
          <option key={kb} value={kb}>{kb}</option>
        ))}
      </select>

      {/* Model */}
      <select
        aria-label="模型"
        value={filter.model ?? ""}
        onChange={(e) => onChange({ ...filter, model: e.target.value || undefined })}
        className="min-h-10 rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600 bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <option value="">全部模型</option>
        {modelOptions.map((m) => (
          <option key={m} value={m}>{m}</option>
        ))}
      </select>

      {/* 来源（2026-10-08 #12）：旅游域/客服/AI 助手三分类，值为后端 DTO source */}
      <select
        aria-label="Trace 来源"
        value={filter.source ?? ""}
        onChange={(e) => onChange({ ...filter, source: e.target.value || undefined })}
        className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600 bg-white focus:outline-none focus:ring-2 focus:ring-violet-500/20"
      >
        <option value="">全部来源</option>
        {Object.entries(TRACE_SOURCE_LABELS).map(([value, label]) => (
          <option key={value} value={value}>{label}</option>
        ))}
      </select>

      {/* Keyword search */}
      <div className="relative flex-1 min-w-[200px]">
        <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" />
        </svg>
        <input
          aria-label="搜索 Trace ID 或工作流"
          type="text"
          placeholder="搜索 Trace ID / Workflow…"
          value={filter.keyword}
          onChange={(e) => onChange({ ...filter, keyword: e.target.value })}
          className="min-h-10 w-full rounded-lg border border-slate-200 pl-9 pr-3 py-1.5 text-xs text-slate-700 placeholder-slate-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        />
      </div>
    </div>
  );
}
