"use client";

import { useState, useEffect, useMemo } from "react";
import Link from "next/link";
import { ShoppingBag, RefreshCw } from "lucide-react";
import TraceBreadcrumb from "@/components/observability/trace/TraceBreadcrumb";
import { listFunnelTraces } from "@/lib/observability/source";
import type { TraceRecord } from "@/types/trace";

/**
 * 选品漏斗运行历史（P1 余量，2026-09-17）。
 *
 * 数据口径：trace.tags 含 funnel_run_id 的主图 trace（漏斗域图跑在主图内，
 * workflow_name 仍是主图，只能以标签为口径；服务端 has_tag=funnel_run_id 过滤）。
 * 指标全部来自适配器埋点：tags.funnel_status/funnel_run_id/funnel_category，
 * metadata.funnel_stage_summary/funnel_duration_ms。
 */

interface StageSummary {
  stage: string;
  kept: number;
  dropped: number;
}

type FunnelStatus = "ok" | "empty_pool" | "need_info" | "failed" | "";

const STATUS_META: Record<FunnelStatus, { label: string; cls: string }> = {
  ok: { label: "正常推荐", cls: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  empty_pool: { label: "淘空", cls: "bg-amber-50 text-amber-700 border-amber-200" },
  need_info: { label: "需补充信息", cls: "bg-sky-50 text-sky-700 border-sky-200" },
  failed: { label: "系统失败", cls: "bg-red-50 text-red-700 border-red-200" },
  "": { label: "未知", cls: "bg-slate-50 text-slate-500 border-slate-200" },
};

// 漏斗主链（brief/report 不进留存链）
const CHAIN_STAGES: Record<string, string> = {
  pool: "建池", screen: "初筛", verify: "验证", econ: "测算", rank: "排序",
};

function statusOf(t: TraceRecord): FunnelStatus {
  const s = t.tags?.funnel_status || "";
  return (s in STATUS_META ? s : "") as FunnelStatus;
}

function fmtDuration(ms: unknown): string {
  const n = Number(ms);
  if (!Number.isFinite(n) || n <= 0) return "-";
  return n >= 1000 ? `${(n / 1000).toFixed(1)}s` : `${Math.round(n)}ms`;
}

function fmtTime(ts: string): string {
  if (!ts) return "-";
  return ts.replace("T", " ").slice(0, 19);
}

export default function SelectionFunnelHistoryPage() {
  const [traces, setTraces] = useState<TraceRecord[]>([]);
  const [mounted, setMounted] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const load = async () => {
    setIsRefreshing(true);
    try {
      setTraces(await listFunnelTraces());
    } finally {
      setIsRefreshing(false);
      setMounted(true);
    }
  };

  useEffect(() => { load(); }, []);

  const sorted = useMemo(
    () => [...traces].sort((a, b) => b.timestamp.localeCompare(a.timestamp)),
    [traces],
  );

  const counts = useMemo(() => {
    const c: Record<FunnelStatus, number> = { ok: 0, empty_pool: 0, need_info: 0, failed: 0, "": 0 };
    for (const t of sorted) c[statusOf(t)] += 1;
    return c;
  }, [sorted]);

  const failureRate = sorted.length
    ? ((counts.failed / sorted.length) * 100).toFixed(0)
    : "0";

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="max-w-[1440px] mx-auto px-6 py-6 space-y-5">
        <TraceBreadcrumb
          crumbs={[{ label: "可观测中心", href: "/observability" }, { label: "选品漏斗" }]}
        />

        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-slate-800 flex items-center gap-2">
              <ShoppingBag size={17} className="text-violet-600" />
              选品漏斗运行历史
            </h1>
            <p className="text-xs text-slate-500 mt-0.5">
              共 {sorted.length} 次运行 · 终态四分：正常 {counts.ok} / 淘空 {counts.empty_pool} / 需补充 {counts.need_info} / 失败 {counts.failed}（失败率 {failureRate}%）
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Link
              href="/observability/traces"
              className="flex items-center gap-1.5 text-xs text-slate-600 hover:text-slate-800 bg-white border border-slate-200 rounded-lg px-3 py-1.5 transition-colors"
            >
              链路追踪
            </Link>
            <button
              onClick={load}
              disabled={isRefreshing}
              className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 bg-white border border-slate-200 rounded-lg px-3 py-1.5 transition-colors disabled:opacity-50"
            >
              <RefreshCw size={13} className={isRefreshing ? "animate-spin" : ""} />
              {isRefreshing ? "刷新中..." : "刷新"}
            </button>
          </div>
        </div>

        {/* Table */}
        <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs font-medium text-slate-500">
                  <th className="py-2.5 px-3 w-32">时间</th>
                  <th className="py-2.5 px-3 w-24">类目</th>
                  <th className="py-2.5 px-3 w-24">终态</th>
                  <th className="py-2.5 px-3">各层留存</th>
                  <th className="py-2.5 px-3 w-20 text-right">漏斗耗时</th>
                  <th className="py-2.5 px-3 w-44">Run ID</th>
                  <th className="py-2.5 px-3 w-20"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {sorted.map((t) => {
                  const st = statusOf(t);
                  const meta = STATUS_META[st];
                  const m = t.metadata as {
                    funnel_stage_summary?: StageSummary[];
                    funnel_duration_ms?: number;
                  };
                  const chain = (m.funnel_stage_summary || [])
                    .filter((s) => CHAIN_STAGES[s.stage])
                    .map((s) => ({
                      label: CHAIN_STAGES[s.stage],
                      kept: s.kept,
                      dropped: s.dropped,
                    }));
                  return (
                    <tr key={t.id} className="hover:bg-slate-50/60">
                      <td className="py-2.5 px-3 text-xs text-slate-500 whitespace-nowrap">
                        {fmtTime(t.timestamp)}
                      </td>
                      <td className="py-2.5 px-3 text-xs text-slate-700">
                        {t.tags?.funnel_category || "-"}
                      </td>
                      <td className="py-2.5 px-3">
                        <span className={`inline-flex text-[11px] px-2 py-0.5 rounded-full border ${meta.cls}`}>
                          {meta.label}
                        </span>
                      </td>
                      <td className="py-2.5 px-3">
                        {chain.length > 0 ? (
                          <div className="flex items-center gap-1 text-[11px] text-slate-600">
                            {chain.map((s, i) => (
                              <span key={s.label} className="flex items-center gap-1">
                                {i > 0 && <span className="text-slate-300">→</span>}
                                <span className="bg-slate-100 rounded px-1.5 py-0.5">
                                  {s.label} <span className="font-medium text-slate-800">{s.kept}</span>
                                  {s.dropped > 0 && <span className="text-red-400"> -{s.dropped}</span>}
                                </span>
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span className="text-xs text-slate-400">-</span>
                        )}
                      </td>
                      <td className="py-2.5 px-3 text-xs text-slate-600 text-right whitespace-nowrap">
                        {fmtDuration(m.funnel_duration_ms)}
                      </td>
                      <td className="py-2.5 px-3 text-xs text-slate-400 font-mono">
                        {t.tags?.funnel_run_id || "-"}
                      </td>
                      <td className="py-2.5 px-3 text-right">
                        <Link
                          href={`/observability/traces/${t.id}`}
                          className="text-xs text-violet-600 hover:text-violet-800 whitespace-nowrap"
                        >
                          详情
                        </Link>
                      </td>
                    </tr>
                  );
                })}
                {mounted && sorted.length === 0 && (
                  <tr>
                    <td colSpan={7} className="py-12 text-center text-sm text-slate-400">
                      暂无漏斗运行记录 —— 在对话里跑一次「智能选品」后这里会出现运行历史
                    </td>
                  </tr>
                )}
                {!mounted && sorted.length === 0 && (
                  <tr>
                    <td colSpan={7} className="py-12 text-center text-sm text-slate-400">
                      加载中...
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <p className="text-xs text-slate-400 leading-relaxed">
          口径说明：漏斗每次运行在主图 trace 上打 funnel_status / funnel_run_id / funnel_category 标签与
          funnel_stage_summary / funnel_duration_ms 指标；「系统失败」是漏斗域图异常降级的软失败终态，
          与「淘空」（候选被正常淘汰光）分开统计，不伪装成正常输出。
        </p>
      </div>
    </div>
  );
}
