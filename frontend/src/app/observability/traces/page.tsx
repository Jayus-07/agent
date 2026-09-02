"use client";

import { useState, useMemo, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import TraceFilterBar from "@/components/observability/trace/TraceFilter";
import StatsBar from "@/components/observability/trace/StatsBar";
import TraceBreadcrumb from "@/components/observability/trace/TraceBreadcrumb";
import TraceRow, { ColKey } from "@/components/observability/trace/TraceRow";
import { useToast } from "@/components/shared/Toast";
import {
  TraceFilter,
  TraceRecord,
  filterByTimeRange,
} from "@/types/trace";
import { listAgentTraces, getAgentTraceStats, AgentTraceStats } from "@/lib/observability/source";

// typedTraces 改为 client 端填充：模块顶层 import 22+ JSON 在 Next.js dev SSR 阶段
// 会导致 typedTraces=[]（webpack transform 22 JSON 的时机问题）；改为 useState + useEffect 后，
// SSR 输出 0 条、client hydrate 后真实数据进来再渲，避免整页空渲染。
const PAGE_SIZES = [20, 50, 100];
const BOOKMARK_KEY = "obs.bookmarks";
const FILTER_KEY = "obs.traceFilter";
const COLUMNS_KEY = "obs.traceColumns";

// 前端时间窗 → 后端 stats 接口的 hours 参数（"custom" 按 24h 处理）
const RANGE_HOURS: Record<string, number> = { "15m": 0.25, "1h": 1, "6h": 6, "24h": 24, custom: 24 };

const DEFAULT_COLUMNS: ColKey[] = ["status", "id", "question", "duration", "tokens", "cost", "session", "time", "actions"];

const ALL_COLUMNS: { key: ColKey; label: string }[] = [
  { key: "status", label: "状态" },
  { key: "id", label: "Trace ID" },
  { key: "question", label: "用户问题" },
  { key: "duration", label: "耗时" },
  { key: "tokens", label: "Token" },
  { key: "cost", label: "成本" },
  { key: "session", label: "Session" },
  { key: "kb", label: "KB" },
  { key: "time", label: "时间" },
  { key: "actions", label: "操作" },
];

export default function TracesPage() {
  const router = useRouter();
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const [sortField, setSortField] = useState<"duration_ms" | "timestamp" | "cost_usd" | "">("");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  // ⚠️ localStorage 不能在 useState 初始化里读（SSR/CSR mismatch 会摧毁整个 root 的 client render）。
  // 统一改为：初始 useState 给 SSR-safe 默认值，client mount 后 useEffect 一次性读 localStorage 覆盖。
  const [filter, setFilter] = useState<TraceFilter>({ timeRange: "1h", status: "all", keyword: "", sortField: "", sortDir: "desc", page: 1, pageSize: 20 });
  const [bookmarks, setBookmarks] = useState<Set<string>>(new Set());
  const [columns, setColumns] = useState<ColKey[]>(DEFAULT_COLUMNS);
  const [typedTraces, setTypedTraces] = useState<TraceRecord[]>([]);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    try {
      const savedFilter = localStorage.getItem(FILTER_KEY);
      if (savedFilter) setFilter(JSON.parse(savedFilter));
      const savedBookmarks = localStorage.getItem(BOOKMARK_KEY);
      if (savedBookmarks) setBookmarks(new Set(JSON.parse(savedBookmarks)));
      const savedCols = localStorage.getItem(COLUMNS_KEY);
      if (savedCols) setColumns(JSON.parse(savedCols));
    } catch {}
    // 数据加载推迟到 mount 后：避免 SSR 阶段同步 IO（mock 时 import 22 JSON；
    // API 时 fetch 也必须在 client 端）
    listAgentTraces().then((traces) => {
      setTypedTraces(traces);
      setMounted(true);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 注意：不再有 [refreshTick, mounted] 的重复拉取 effect ——
  // 初始挂载上方已拉一次，手动刷新由 handleRefresh 直接拉，避免双重请求。

  const [showColumns, setShowColumns] = useState(false);
  const [compareIds, setCompareIds] = useState<Set<string>>(new Set());
  const toast = useToast();

  useEffect(() => {
    localStorage.setItem(FILTER_KEY, JSON.stringify(filter));
  }, [filter]);
  useEffect(() => {
    localStorage.setItem(BOOKMARK_KEY, JSON.stringify(Array.from(bookmarks)));
  }, [bookmarks]);
  useEffect(() => {
    localStorage.setItem(COLUMNS_KEY, JSON.stringify(columns));
  }, [columns]);

  const handleRefresh = async () => {
    setIsRefreshing(true);
    // 触发真实数据拉取（mock 也走异步路径，保持一致 UX）
    try {
      const traces = await listAgentTraces();
      setTypedTraces(traces);
    } finally {
      setIsRefreshing(false);
    }
  };

  const filtered = useMemo(() => {
    let arr = filterByTimeRange(typedTraces, filter.timeRange);

    if (filter.status !== "all") {
      arr = arr.filter((t) => {
        const hasError = t.error && Object.keys(t.error).length > 0;
        if (filter.status === "error") return hasError || t.status === "error";
        if (filter.status === "success") return !hasError && t.status !== "error";
        // 超时判定用后端下发的 SLA breached（按链路类型 30-90s），
        // 不再用 duration>5000ms 硬阈值（会把正常慢请求误标 TIMEOUT）
        if (filter.status === "timeout") return t.sla?.breached || t.status === "timeout";
        if (filter.status === "cancelled") return t.status === "cancelled";
        return true;
      });
    }

    if (filter.kb_id) arr = arr.filter((t) => String(t.metadata?.kb_id) === filter.kb_id);
    if (filter.model) arr = arr.filter((t) => t.model?.name === filter.model);

    if (filter.keyword.trim()) {
      const kw = filter.keyword.toLowerCase();
      // 前缀匹配支持（trace id 前 8 位 / session 前缀）
      arr = arr.filter((t) =>
        t.question.toLowerCase().includes(kw) ||
        t.answer_preview.toLowerCase().includes(kw) ||
        t.session_id.toLowerCase().includes(kw) ||
        t.id.toLowerCase().startsWith(kw) ||
        t.id.toLowerCase().includes(kw)
      );
    }

    if (sortField === "duration_ms") {
      arr = [...arr].sort((a, b) => sortDir === "desc" ? b.duration_ms - a.duration_ms : a.duration_ms - b.duration_ms);
    } else if (sortField === "timestamp") {
      arr = [...arr].sort((a, b) => sortDir === "desc" ? b.timestamp.localeCompare(a.timestamp) : a.timestamp.localeCompare(b.timestamp));
    } else if (sortField === "cost_usd") {
      arr = [...arr].sort((a, b) => sortDir === "desc" ? (b.cost_usd ?? 0) - (a.cost_usd ?? 0) : (a.cost_usd ?? 0) - (b.cost_usd ?? 0));
    }

    return arr;
  }, [typedTraces, filter, sortField, sortDir]);

  const total = filtered.length;
  const traces = useMemo(() => filtered.slice((page - 1) * pageSize, page * pageSize), [filtered, page, pageSize]);

  // stats 下沉后端（/traces/stats）：不再客户端遍历 200 条；
  // 接口失败（返回 null）时降级为本地计算，口径不变。
  const [serverStats, setServerStats] = useState<AgentTraceStats | null>(null);
  useEffect(() => {
    if (!mounted) return;
    getAgentTraceStats(RANGE_HOURS[filter.timeRange] ?? 24).then(setServerStats);
  }, [mounted, filter.timeRange, typedTraces]);

  const stats = useMemo(() => {
    if (serverStats) return serverStats;
    const inRange = filterByTimeRange(typedTraces, filter.timeRange);
    return {
      total_24h: inRange.length,
      success_rate: inRange.filter(t => !(t.error && Object.keys(t.error).length > 0) && t.status !== "error").length / (inRange.length || 1),
      avg_duration_ms: inRange.length ? Math.round(inRange.reduce((s, t) => s + t.duration_ms, 0) / inRange.length) : 0,
      p95_duration_ms: inRange.length ? [...inRange].sort((a, b) => b.duration_ms - a.duration_ms)[Math.floor(inRange.length * 0.05)]?.duration_ms ?? 0 : 0,
      error_count: inRange.filter(t => (t.error && Object.keys(t.error).length > 0) || t.status === "error").length,
      total_cost_usd: inRange.reduce((s, t) => s + (t.cost_usd ?? 0), 0),
    };
    // 注意：本地降级口径只反映「时间窗内全部 trace」的聚合（不被 status/kb/model/keyword 影响），
    // 与下方 header 的「共 N 条」(filtered) 是不同口径。
  }, [serverStats, typedTraces, filter.timeRange]);

  const totalPages = Math.ceil(total / pageSize);

  const handleSort = (field: "duration_ms" | "timestamp" | "cost_usd") => {
    if (sortField === field) {
      setSortDir(sortDir === "desc" ? "asc" : "desc");
    } else {
      setSortField(field);
      setSortDir("desc");
    }
  };

  // 稳定回调（TraceRow memo 的前提）：引用稳定后，无关状态变化不再触发整表重渲。
  // stopPropagation 已移入 TraceRow 内部。
  const handleNavigate = useCallback((target: string) => {
    router.push(target.startsWith("/") ? target : `/observability/traces/${target}`);
  }, [router]);

  const handleCopy = useCallback((id: string) => {
    navigator.clipboard.writeText(id);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 1500);
  }, []);

  const toggleBookmark = useCallback((id: string) => {
    setBookmarks((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }, []);

  const toggleCompare = useCallback((id: string) => {
    setCompareIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
        return next;
      }
      if (next.size >= 4) {
        toast.warning("最多对比 4 个 trace");
        return prev;
      }
      next.add(id);
      return next;
    });
  }, [toast]);

  const goCompare = () => {
    if (compareIds.size < 2) return;
    router.push(`/observability/traces/compare?ids=${Array.from(compareIds).join(",")}`);
  };

  const exportCsv = () => {
    const headers = ["id", "timestamp", "session_id", "question", "duration_ms", "tokens", "cost_usd", "status", "kb_id"];
    const rows = filtered.map((t) => [
      t.id, t.timestamp, t.session_id, `"${t.question.replace(/"/g, '""')}"`,
      t.duration_ms, t.usage?.total_tokens ?? 0, t.cost_usd ?? 0,
      t.status ?? "success", String(t.metadata?.kb_id ?? ""),
    ].join(","));
    const csv = [headers.join(","), ...rows].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `traces-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const SortIcon = ({ field }: { field: string }) => (
    <span className="text-slate-300 ml-1">
      {sortField === field ? (sortDir === "desc" ? "↓" : "↑") : "↕"}
    </span>
  );

  const has = (k: ColKey) => columns.includes(k);

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="max-w-[1440px] mx-auto px-6 py-6 space-y-5">
        {/* Breadcrumb */}
        <TraceBreadcrumb crumbs={[{ label: "可观测中心", href: "/observability" }, { label: "链路追踪" }]} />

        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-slate-800">链路追踪</h1>
            <p className="text-xs text-slate-500 mt-0.5">共 {total} 条 Trace · {filter.timeRange} 筛选</p>
          </div>
          <div className="flex items-center gap-2">
            {compareIds.size >= 2 && (
              <button
                onClick={goCompare}
                className="flex items-center gap-1.5 text-xs text-white bg-violet-600 hover:bg-violet-700 rounded-lg px-3 py-1.5 transition-colors"
              >
                ⚖️ 对比 ({compareIds.size})
              </button>
            )}
            <button
              onClick={exportCsv}
              className="flex items-center gap-1.5 text-xs text-slate-600 hover:text-slate-800 bg-white border border-slate-200 rounded-lg px-3 py-1.5 transition-colors"
            >
              📥 导出 CSV
            </button>
            <button
              onClick={handleRefresh}
              disabled={isRefreshing}
              className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 bg-white border border-slate-200 rounded-lg px-3 py-1.5 transition-colors disabled:opacity-50"
            >
              <svg className={`w-3.5 h-3.5 ${isRefreshing ? "animate-spin" : ""}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 12a9 9 0 11-2.2-5.9M21 3v6h-6" /></svg>
              {isRefreshing ? "刷新中..." : "Refresh"}
            </button>
          </div>
        </div>

        {/* Stats */}
        <StatsBar stats={stats} traceCount={total} />

        {/* Filters */}
        <div className="bg-white border border-slate-200 rounded-xl p-3 space-y-2">
          <TraceFilterBar filter={filter} onChange={(f) => { setFilter(f); setPage(1); }} traces={typedTraces} />
          {/* Column picker */}
          <div className="relative border-t border-slate-100 pt-2 flex items-center gap-2">
            <button
              onClick={() => setShowColumns(!showColumns)}
              className="text-[10px] text-slate-500 hover:text-slate-700 flex items-center gap-1"
            >
              ⚙️ 列设置 ({columns.length}/{ALL_COLUMNS.length})
            </button>
            {showColumns && (
              <div className="absolute top-full left-0 mt-1 z-20 bg-white border border-slate-200 rounded-lg shadow-lg p-2 flex flex-wrap gap-1.5 max-w-[600px]">
                {ALL_COLUMNS.map((c) => (
                  <label key={c.key} className="flex items-center gap-1 text-xs text-slate-600 hover:bg-slate-50 px-2 py-1 rounded cursor-pointer">
                    <input
                      type="checkbox"
                      checked={columns.includes(c.key)}
                      onChange={() => {
                        const next = columns.includes(c.key)
                          ? columns.filter((x) => x !== c.key)
                          : [...columns, c.key];
                        setColumns(next);
                      }}
                      className="rounded"
                    />
                    {c.label}
                  </label>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Table */}
        <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs font-medium text-slate-500">
                  {has("status") && <th className="py-3 px-4 w-10">状态</th>}
                  {has("id") && <th className="py-3 px-4">Trace ID</th>}
                  {has("question") && <th className="py-3 px-4">用户问题</th>}
                  {has("duration") && (
                    <th className="py-3 px-4 w-24 text-right cursor-pointer select-none" onClick={() => handleSort("duration_ms")}>
                      耗时<SortIcon field="duration_ms" />
                    </th>
                  )}
                  {has("tokens") && <th className="py-3 px-4 w-28">Token</th>}
                  {has("cost") && (
                    <th className="py-3 px-4 w-24 text-right cursor-pointer select-none" onClick={() => handleSort("cost_usd")}>
                      成本<SortIcon field="cost_usd" />
                    </th>
                  )}
                  {has("session") && <th className="py-3 px-4">Session</th>}
                  {has("kb") && <th className="py-3 px-4 w-28">KB</th>}
                  {has("time") && (
                    <th className="py-3 px-4 w-32 cursor-pointer select-none" onClick={() => handleSort("timestamp")}>
                      时间<SortIcon field="timestamp" />
                    </th>
                  )}
                  {has("actions") && <th className="py-3 px-4 w-20"></th>}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {traces.map((t) => (
                  <TraceRow
                    key={t.id}
                    t={t}
                    columns={columns}
                    isBookmarked={bookmarks.has(t.id)}
                    isCompared={compareIds.has(t.id)}
                    isCopied={copiedId === t.id}
                    onNavigate={handleNavigate}
                    onCopy={handleCopy}
                    onToggleBookmark={toggleBookmark}
                    onToggleCompare={toggleCompare}
                  />
                ))}
                {traces.length === 0 && (
                  <tr>
                    <td colSpan={columns.length} className="py-12 text-center text-sm text-slate-400">
                      当前筛选条件下无 trace
                    </td>
                  </tr>
                )}
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