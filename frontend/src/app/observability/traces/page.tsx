"use client";

import { useState, useMemo } from "react";
import { useRouter } from "next/navigation";
import TraceFilterBar from "@/components/observability/trace/TraceFilter";
import StatsBar from "@/components/observability/trace/StatsBar";
import { TraceFilter, TraceRecord, statusBadge, durationColor, durationBg, formatTime, truncate } from "@/types/trace";
import mockTraces from "@/mock/traces.json";

const typedTraces = mockTraces as unknown as TraceRecord[];
const PAGE_SIZES = [20, 50, 100];

export default function TracesPage() {
  const router = useRouter();
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [sortField, setSortField] = useState<"duration_ms" | "timestamp" | "">("");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const [filter, setFilter] = useState<TraceFilter>({
    timeRange: "1h", status: "all", keyword: "",
    sortField: "", sortDir: "desc", page: 1, pageSize: 20,
  });

  const traces = useMemo(() => {
    let filtered = [...typedTraces];

    if (filter.status !== "all") {
      filtered = filtered.filter((t) => {
        const hasError = t.error && Object.keys(t.error).length > 0;
        if (filter.status === "error") return hasError || t.status === "error";
        if (filter.status === "success") return !hasError && t.status !== "error";
        if (filter.status === "timeout") return t.duration_ms > 5000 || t.status === "timeout";
        if (filter.status === "cancelled") return t.status === "cancelled";
        return true;
      });
    }

    if (filter.keyword.trim()) {
      const kw = filter.keyword.toLowerCase();
      filtered = filtered.filter((t) =>
        t.question.toLowerCase().includes(kw) || t.answer_preview.toLowerCase().includes(kw) || t.session_id.toLowerCase().includes(kw)
      );
    }

    if (sortField === "duration_ms") {
      filtered.sort((a, b) => sortDir === "desc" ? b.duration_ms - a.duration_ms : a.duration_ms - b.duration_ms);
    } else if (sortField === "timestamp") {
      filtered.sort((a, b) => sortDir === "desc" ? b.timestamp.localeCompare(a.timestamp) : a.timestamp.localeCompare(b.timestamp));
    }

    const start = (page - 1) * pageSize;
    return filtered.slice(start, start + pageSize);
  }, [filter, sortField, sortDir, page, pageSize]);

  const total = useMemo(() => {
    let filtered = [...typedTraces];
    if (filter.keyword.trim()) {
      const kw = filter.keyword.toLowerCase();
      filtered = filtered.filter((t) => t.question.toLowerCase().includes(kw) || t.answer_preview.toLowerCase().includes(kw));
    }
    return filtered.length;
  }, [filter]);

  const stats = useMemo(() => ({
    total_24h: typedTraces.length,
    success_rate: typedTraces.filter(t => !(t.error && Object.keys(t.error).length > 0) && t.status !== "error").length / typedTraces.length,
    avg_duration_ms: Math.round(typedTraces.reduce((s, t) => s + t.duration_ms, 0) / typedTraces.length),
    p95_duration_ms: [...typedTraces].sort((a, b) => b.duration_ms - a.duration_ms)[Math.floor(typedTraces.length * 0.05)]?.duration_ms ?? 0,
    error_count: typedTraces.filter(t => (t.error && Object.keys(t.error).length > 0) || t.status === "error").length,
  }), []);

  const totalPages = Math.ceil(total / pageSize);

  const handleSort = (field: "duration_ms" | "timestamp") => {
    if (sortField === field) {
      setSortDir(sortDir === "desc" ? "asc" : "desc");
    } else {
      setSortField(field);
      setSortDir("desc");
    }
  };

  const SortIcon = ({ field }: { field: string }) => (
    <span className="text-slate-300 ml-1">
      {sortField === field ? (sortDir === "desc" ? "↓" : "↑") : "↕"}
    </span>
  );

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="max-w-[1440px] mx-auto px-6 py-6 space-y-5">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-slate-800">链路追踪</h1>
            <p className="text-xs text-slate-500 mt-0.5">共 {total} 条 Trace</p>
          </div>
          <button
            onClick={() => { setPage(1); }}
            className="flex items-center gap-2 text-xs text-slate-500 hover:text-slate-700 bg-white border border-slate-200 rounded-lg px-3 py-1.5 transition-colors"
          >
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 12a9 9 0 11-2.2-5.9M21 3v6h-6" /></svg>
            Refresh
          </button>
        </div>

        {/* Stats */}
        <StatsBar stats={stats} traceCount={total} />

        {/* Filters */}
        <div className="bg-white border border-slate-200 rounded-xl p-3">
          <TraceFilterBar filter={filter} onChange={(f) => { setFilter(f); setPage(1); }} />
        </div>

        {/* Table */}
        <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs font-medium text-slate-500">
                  <th className="py-3 px-4 w-10">状态</th>
                  <th className="py-3 px-4">Trace ID</th>
                  <th className="py-3 px-4">用户问题</th>
                  <th className="py-3 px-4 w-20 text-right cursor-pointer select-none" onClick={() => handleSort("duration_ms")}>耗时<SortIcon field="duration_ms" /></th>
                  <th className="py-3 px-4 w-28">Token</th>
                  <th className="py-3 px-4">Session</th>
                  <th className="py-3 px-4 w-28 cursor-pointer select-none" onClick={() => handleSort("timestamp")}>时间<SortIcon field="timestamp" /></th>
                  <th className="py-3 px-4 w-20"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {traces.map((t) => {
                  const stat = (t.error && Object.keys(t.error).length > 0) || t.status === "error" ? "error"
                    : t.status === "timeout" || t.duration_ms > 5000 ? "timeout" : "success";
                  const badge = statusBadge(stat);
                  const maxStepMs = Math.max(...t.steps.map(s => s.duration_ms), 0);
                  const topSteps = [...t.steps].sort((a, b) => b.duration_ms - a.duration_ms).slice(0, 3);

                  return (
                    <tr
                      key={t.id}
                      onClick={() => router.push(`/observability/traces/${t.id}`)}
                      className={`group cursor-pointer transition-colors hover:bg-slate-50 ${durationBg(t.duration_ms)}`}
                    >
                      <td className="py-3 px-4">
                        <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold ${badge.bg}`}>{badge.label}</span>
                      </td>
                      <td className="py-3 px-4">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-xs text-slate-600">{t.id.slice(0, 8)}...</span>
                          <button onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(t.id); setCopiedId(t.id); setTimeout(() => setCopiedId(null), 1500); }} className="opacity-0 group-hover:opacity-100 text-slate-400 hover:text-slate-600 transition-opacity" title="复制">
                            {copiedId === t.id ? <span className="text-[10px] text-emerald-500">已复制</span> : <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" /></svg>}
                          </button>
                        </div>
                      </td>
                      <td className="py-3 px-4"><span className="text-slate-700 line-clamp-1" title={t.question}>{truncate(t.question, 50)}</span></td>
                      <td className="py-3 px-4 text-right">
                        <span className={`font-mono tabular-nums font-semibold ${durationColor(t.duration_ms)}`} title={topSteps.map(s => `${s.label}: ${s.duration_ms}ms`).join("\n")}>
                          {t.duration_ms}ms
                        </span>
                      </td>
                      <td className="py-3 px-4">
                        <div className="flex items-center gap-1 text-xs font-mono tabular-nums">
                          <span className="text-slate-400">{t.usage?.prompt_tokens ?? 0}</span>
                          <span className="text-slate-300">/</span>
                          <span className="text-slate-600 font-medium">{t.usage?.completion_tokens ?? 0}</span>
                        </div>
                      </td>
                      <td className="py-3 px-4"><span className="text-xs font-mono text-slate-400 bg-slate-100 rounded px-1.5 py-0.5">{t.session_id.slice(0, 16)}</span></td>
                      <td className="py-3 px-4 text-xs text-slate-400 font-mono">{formatTime(t.timestamp)}</td>
                      <td className="py-3 px-4"><span className="text-xs text-violet-600 font-medium opacity-0 group-hover:opacity-100 transition-opacity">详情 →</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between px-4 py-3 border-t border-slate-100">
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-400">每页</span>
              <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }} className="text-xs border border-slate-200 rounded px-2 py-1">
                {PAGE_SIZES.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
              <span className="text-xs text-slate-400">条 · {total} 条共 {totalPages} 页</span>
            </div>
            <div className="flex items-center gap-1">
              <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} className="text-xs px-3 py-1 border border-slate-200 rounded disabled:opacity-30 disabled:cursor-not-allowed hover:bg-slate-50">上一页</button>
              <span className="text-xs text-slate-500 px-2">{page}</span>
              <button disabled={page >= totalPages} onClick={() => setPage(p => p + 1)} className="text-xs px-3 py-1 border border-slate-200 rounded disabled:opacity-30 disabled:cursor-not-allowed hover:bg-slate-50">下一页</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
