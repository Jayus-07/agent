"use client";

import { useEffect, useState } from "react";
import { getCsQualityReport, CSQualityReport, CSVariantSummary } from "@/api/observability";

/**
 * CS 灰度质量卡片 — treatment(CS graph) vs control(主图) 灰度对比
 *
 * 数据源：GET /api/observability/cs-quality
 * 展示：路由一致率 / 兜底率 / 转人工率（三桶）/ P95 时延 + 告警条。
 * 无 CS 灰度数据时收成一行占位，不占版面。
 */

const PERCENT = (v: number | null | undefined) =>
  v === null || v === undefined ? "--" : `${(v * 100).toFixed(1)}%`;

const REASON_LABELS: Record<string, string> = {
  healthy_user: "用户主动",
  healthy_escalation: "业务升级",
  capability_gap: "能力缺口",
};

function MetricRow({ label, treat, control, invert = false }: {
  label: string;
  treat: number | null;
  control: number | null;
  /** invert=true 时数值越低越好（兜底率/转人工率），treatment 更差时标红 */
  invert?: boolean;
}) {
  const treatWorse = treat !== null && control !== null &&
    (invert ? treat > control : treat < control);
  return (
    <div className="flex items-center justify-between py-1.5 text-xs">
      <span className="text-slate-500">{label}</span>
      <span className="flex items-center gap-3 font-mono tabular-nums">
        <span className={treatWorse ? "text-red-600 font-semibold" : "text-slate-700"}>
          {PERCENT(treat)}
        </span>
        <span className="text-slate-300">vs</span>
        <span className="text-slate-400">{PERCENT(control)}</span>
      </span>
    </div>
  );
}

function HandoffBreakdown({ s }: { s: CSVariantSummary }) {
  const entries = Object.entries(s.handoff_by_reason || {}).filter(([, n]) => n > 0);
  if (entries.length === 0) return <span className="text-slate-400">--</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {entries.map(([bucket, n]) => (
        <span
          key={bucket}
          title={REASON_LABELS[bucket] || bucket}
          className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${
            bucket === "capability_gap"
              ? "bg-red-50 text-red-600"
              : "bg-slate-100 text-slate-500"
          }`}
        >
          {REASON_LABELS[bucket] || bucket} {n}
        </span>
      ))}
    </span>
  );
}

function VariantColumn({ label, s, highlight }: { label: string; s: CSVariantSummary | undefined; highlight?: boolean }) {
  if (!s) return null;
  return (
    <div className={`flex-1 rounded-lg p-3 ${highlight ? "bg-violet-50/60 border border-violet-200" : "bg-slate-50 border border-slate-100"}`}>
      <div className="flex items-center gap-2 mb-2">
        <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${highlight ? "bg-violet-600 text-white" : "bg-slate-200 text-slate-600"}`}>
          {label}
        </span>
        <span className="text-[10px] text-slate-400 font-mono">{s.total} 会话</span>
      </div>
      {s.total === 0 ? (
        <p className="text-[11px] text-slate-400">无数据</p>
      ) : (
        <div className="space-y-0">
          <MetricRow label="路由一致率" treat={s.route_consistency} control={null} />
          <MetricRow label="兜底率" treat={s.fallback_rate} control={null} invert />
          <div className="flex items-center justify-between py-1.5 text-xs">
            <span className="text-slate-500">转人工率</span>
            <span className="flex items-center gap-2">
              <span className="font-mono tabular-nums text-slate-700">{PERCENT(s.handoff_rate)}</span>
              <HandoffBreakdown s={s} />
            </span>
          </div>
          <div className="flex items-center justify-between py-1.5 text-xs">
            <span className="text-slate-500">P95 时延</span>
            <span className="font-mono tabular-nums text-slate-700">
              {s.p95_ms === null ? "--" : `${s.p95_ms}ms`}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

export default function CSQualityCard({ hours = 24 }: { hours?: number }) {
  const [report, setReport] = useState<CSQualityReport | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getCsQualityReport(hours).then((r) => {
      if (cancelled) return;
      setReport(r);
      setLoaded(true);
      // 有告警时默认展开，吸引注意
      if (r && (r.alerts?.length ?? 0) > 0) setExpanded(true);
    });
    return () => { cancelled = true; };
  }, [hours]);

  if (!loaded) return null;
  if (!report || report.total_cs_traces === 0) {
    // 无 CS 灰度数据：收成一行占位
    return (
      <div className="bg-white border border-slate-200 rounded-xl px-4 py-2.5 flex items-center gap-2">
        <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-slate-100 text-slate-500">CS 灰度</span>
        <span className="text-[11px] text-slate-400">暂无客服域灰度数据（CS_ENABLED 未启用或无流量）</span>
      </div>
    );
  }

  const treat = report.by_variant?.treatment;
  const control = report.by_variant?.control;

  return (
    <div className="bg-white border border-slate-200 rounded-xl p-4 space-y-3">
      {/* Alerts */}
      {report.alerts?.length > 0 && (
        <div className="space-y-1">
          {report.alerts.map((a, i) => (
            <div key={i} className={`text-xs rounded-lg px-3 py-2 flex items-center gap-2 ${
              a.severity === "error" ? "bg-red-50 text-red-700 border border-red-200" : "bg-amber-50 text-amber-700 border border-amber-200"
            }`}>
              <span>{a.severity === "error" ? "🚨" : "⚠️"}</span>
              <span>{a.message}</span>
            </div>
          ))}
        </div>
      )}

      {/* Header */}
      <button onClick={() => setExpanded(!expanded)} className="w-full flex items-center gap-2 text-left">
        <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-violet-100 text-violet-700">CS 灰度质量</span>
        <span className="text-[11px] text-slate-400">
          近 {report.window_hours}h · {report.total_cs_traces} 条客服会话
        </span>
        <span className="ml-auto text-[10px] text-slate-400">{expanded ? "收起 ▲" : "展开 ▼"}</span>
      </button>

      {/* Treatment vs Control */}
      {expanded && (
        <div className="flex gap-3">
          <VariantColumn label="Treatment · CS Graph" s={treat} highlight />
          <VariantColumn label="Control · 主图" s={control} />
        </div>
      )}
    </div>
  );
}
