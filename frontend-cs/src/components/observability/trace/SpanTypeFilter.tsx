"use client";

import { Span, SPAN_TYPE_LABELS, spanTypeColor } from "@/types/trace";

/**
 * Span Type 过滤器 —— Checkbox 按 type 过滤 Timeline
 * 使用频率极高：120 个 Span 时，只想看 LLM 调用
 */

interface Props {
  spans: Span[];
  activeTypes: Set<string>;
  onChange: (types: Set<string>) => void;
}

export default function SpanTypeFilter({ spans, activeTypes, onChange }: Props) {
  // 统计每种 type
  const counts = new Map<string, number>();
  for (const s of spans) {
    counts.set(s.type, (counts.get(s.type) || 0) + 1);
  }
  const types = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);

  const allSelected = activeTypes.size === 0 || types.every(([t]) => activeTypes.has(t));

  const toggle = (type: string) => {
    const next = new Set(activeTypes.size === 0 ? types.map(([t]) => t) : activeTypes);
    if (next.has(type)) {
      next.delete(type);
    } else {
      next.add(type);
    }
    // 全选时清空（表示不限制）
    if (next.size === types.length) {
      onChange(new Set());
    } else {
      onChange(next);
    }
  };

  const toggleAll = () => {
    onChange(allSelected ? new Set(types.map(([t]) => t)) : new Set());
  };

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <button
        onClick={toggleAll}
        className={`text-[10px] px-2 py-0.5 rounded-full transition-colors ${
          allSelected ? "bg-slate-700 text-white" : "bg-slate-100 text-slate-500 hover:bg-slate-200"
        }`}
      >
        {allSelected ? "全部" : "反选"}
      </button>
      {types.map(([type, count]) => {
        const isActive = activeTypes.size === 0 || activeTypes.has(type);
        return (
          <button
            key={type}
            onClick={() => toggle(type)}
            className={`flex items-center gap-1.5 text-[10px] px-2 py-0.5 rounded-full transition-colors ${
              isActive
                ? "bg-slate-100 text-slate-700 ring-1 ring-slate-300"
                : "bg-white text-slate-300 line-through"
            }`}
          >
            <span className={`inline-block w-2 h-2 rounded-sm ${isActive ? spanTypeColor(type) : "bg-slate-200"}`} />
            <span>{SPAN_TYPE_LABELS[type] || type}</span>
            <span className={isActive ? "text-slate-400" : "text-slate-300"}>{count}</span>
          </button>
        );
      })}
    </div>
  );
}
