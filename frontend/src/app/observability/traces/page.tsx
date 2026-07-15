"use client";

import { useState, useMemo } from "react";
import TraceTable from "@/components/observability/trace/TraceTable";
import TraceFilterBar from "@/components/observability/trace/TraceFilter";
import { TraceFilter, TraceRecord } from "@/types/trace";
import mockTraces from "@/mock/traces.json";

const typedTraces = mockTraces as unknown as TraceRecord[];

export default function TracesPage() {
  const [filter, setFilter] = useState<TraceFilter>({
    timeRange: "1h",
    status: "all",
    appName: "",
    keyword: "",
  });

  const traces = useMemo(() => {
    let filtered = typedTraces;

    // Status
    if (filter.status !== "all") {
      filtered = filtered.filter((t) => {
        const hasError = t.error && Object.keys(t.error).length > 0;
        if (filter.status === "error") return hasError;
        if (filter.status === "success") return !hasError;
        if (filter.status === "timeout") return t.duration_ms > 5000;
        return true;
      });
    }

    // Keyword
    if (filter.keyword.trim()) {
      const kw = filter.keyword.toLowerCase();
      filtered = filtered.filter(
        (t) =>
          t.question.toLowerCase().includes(kw) ||
          t.answer_preview.toLowerCase().includes(kw) ||
          t.session_id.toLowerCase().includes(kw)
      );
    }

    return filtered;
  }, [filter]);

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="max-w-[1440px] mx-auto px-6 py-6 space-y-5">
        {/* Page header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-slate-800">链路追踪</h1>
            <p className="text-xs text-slate-500 mt-0.5">
              共 {traces.length} 条 Trace
            </p>
          </div>
        </div>

        {/* Filters */}
        <div className="bg-white border border-slate-200 rounded-xl p-3">
          <TraceFilterBar filter={filter} onChange={setFilter} />
        </div>

        {/* Table */}
        <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
          <TraceTable traces={traces} />
        </div>
      </div>
    </div>
  );
}
