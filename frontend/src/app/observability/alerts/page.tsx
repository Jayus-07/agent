"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { listAllTraces } from "@/lib/observability/source";
import TraceBreadcrumb from "@/components/observability/trace/TraceBreadcrumb";
import {
  TraceRecord,
  AlertItem,
  severityStyle,
  formatBoth,
} from "@/types/trace";

export default function AlertsPage() {
  const [resolved, setResolved] = useState<Set<string>>(new Set());
  const [typedTraces, setTypedTraces] = useState<TraceRecord[]>([]);
  const [loading, setLoading] = useState(true);

  // 告警页需要 spans（slow step / rerank zero 检测），异步加载所有 trace
  useEffect(() => {
    listAllTraces().then((traces) => {
      setTypedTraces(traces);
      setLoading(false);
    });
  }, []);

  // 从 trace 数据动态聚合告警
  const alerts = useMemo(() => buildAlerts(typedTraces), [typedTraces]);

  const filtered = alerts.filter((a) => (resolved.has(a.id) ? false : true));

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="text-center text-sm text-slate-400">加载中…</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="max-w-[1440px] mx-auto px-6 py-6 space-y-5">
        <TraceBreadcrumb crumbs={[{ label: "可观测中心", href: "/observability" }, { label: "告警中心" }]} />

        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-slate-800">告警中心</h1>
            <p className="text-xs text-slate-500 mt-0.5">从近 24h trace 自动聚合 · {filtered.length} 条未处理</p>
          </div>
          <button
            onClick={() => setResolved(new Set(alerts.map((a) => a.id)))}
            className="text-xs text-slate-500 hover:text-slate-700 bg-white border border-slate-200 rounded-lg px-3 py-1.5"
          >
            全部标记已处理
          </button>
        </div>

        {/* 概览 */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <SeverityCard severity="critical" count={alerts.filter((a) => a.severity === "critical").length} label="严重" />
          <SeverityCard severity="error" count={alerts.filter((a) => a.severity === "error").length} label="错误" />
          <SeverityCard severity="warning" count={alerts.filter((a) => a.severity === "warning").length} label="警告" />
          <div className="bg-white border border-slate-200 rounded-xl px-4 py-3">
            <p className="text-[10px] uppercase tracking-widest text-slate-400 mb-1">已处理</p>
            <p className="text-lg font-bold font-mono text-emerald-600">{resolved.size}</p>
          </div>
        </div>

        {/* 告警列表 */}
        <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
          {filtered.length === 0 ? (
            <div className="py-16 text-center text-sm text-slate-400">🎉 当前无未处理告警</div>
          ) : (
            <div className="divide-y divide-slate-100">
              {filtered.map((a) => {
                const sev = severityStyle(a.severity);
                return (
                  <div key={a.id} className="px-4 py-4 hover:bg-slate-50 transition-colors">
                    <div className="flex items-start gap-3">
                      <span className={`shrink-0 inline-block px-2 py-0.5 rounded text-[10px] font-semibold ${sev.bg}`}>
                        {sev.label}
                      </span>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-slate-800">{a.message}</p>
                        <p className="text-[10px] text-slate-400 mt-1 font-mono">{formatBoth(a.created_at)} · 类型: {a.type}</p>
                        {a.trace_ids.length > 0 && (
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {a.trace_ids.slice(0, 5).map((tid) => (
                              <Link
                                key={tid}
                                href={`/observability/traces/${tid}`}
                                className="text-[10px] font-mono text-violet-600 hover:text-violet-800 bg-violet-50 hover:bg-violet-100 rounded px-2 py-0.5"
                              >
                                {tid.slice(0, 12)}…
                              </Link>
                            ))}
                            {a.trace_ids.length > 5 && (
                              <span className="text-[10px] text-slate-400">+{a.trace_ids.length - 5} more</span>
                            )}
                          </div>
                        )}
                      </div>
                      <button
                        onClick={() => setResolved((prev) => new Set(prev).add(a.id))}
                        className="text-[10px] text-emerald-600 hover:text-emerald-800 border border-emerald-200 hover:border-emerald-400 rounded px-2 py-1 shrink-0"
                      >
                        ✓ 处理
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function SeverityCard({ severity, count, label }: { severity: AlertItem["severity"]; count: number; label: string }) {
  const sev = severityStyle(severity);
  return (
    <div className="bg-white border border-slate-200 rounded-xl px-4 py-3">
      <p className="text-[10px] uppercase tracking-widest text-slate-400 mb-1">{label}</p>
      <p className={`text-lg font-bold font-mono ${sev.text}`}>{count}</p>
    </div>
  );
}

function buildAlerts(traces: TraceRecord[]): AlertItem[] {
  const alerts: AlertItem[] = [];

  // 1) SLA 违反（>5s）
  const slaViolations = traces.filter((t) => t.sla?.breached);
  if (slaViolations.length > 0) {
    alerts.push({
      id: "sla-breach-summary",
      severity: "warning",
      type: "sla_breach",
      message: `${slaViolations.length} 条 trace 超过 SLA 阈值（5s），平均耗时 ${(slaViolations.reduce((s, t) => s + t.duration_ms, 0) / slaViolations.length / 1000).toFixed(2)}s`,
      trace_ids: slaViolations.map((t) => t.id),
      created_at: slaViolations[0].timestamp,
    });
  }

  // 2) 错误率高 / 错误集中
  const errors = traces.filter((t) => t.error && Object.keys(t.error).length > 0);
  if (errors.length > 0) {
    const rate = errors.length / traces.length;
    const byCode: Record<string, number> = {};
    errors.forEach((e) => {
      const code = String((e.error as any).code ?? "UNKNOWN");
      byCode[code] = (byCode[code] ?? 0) + 1;
    });
    const topCode = Object.entries(byCode).sort((a, b) => b[1] - a[1])[0];

    alerts.push({
      id: "error-rate",
      severity: rate > 0.2 ? "critical" : "error",
      type: "error_rate",
      message: `错误率 ${(rate * 100).toFixed(1)}%（${errors.length}/${traces.length}），主要错误: ${topCode[0]} (${topCode[1]} 次)`,
      trace_ids: errors.map((t) => t.id),
      created_at: errors[0].timestamp,
    });
  }

  // 3) LLM 限流（critical）
  const rateLimited = errors.filter((t) => String((t.error as any).code) === "LLM_RATE_LIMIT");
  if (rateLimited.length > 0) {
    alerts.push({
      id: "llm-rate-limit",
      severity: "critical",
      type: "cost_anomaly",
      message: `LLM 服务限流 ${rateLimited.length} 次，建议检查 API 配额或启用降级策略`,
      trace_ids: rateLimited.map((t) => t.id),
      created_at: rateLimited[0].timestamp,
    });
  }

  // 4) 高耗时 span 异常
  traces.forEach((t) => {
    const spans = t.spans || [];
    const slowSpan = spans.find((s) => s.duration_ms > 3000 && s.status !== "skipped");
    if (slowSpan) {
      alerts.push({
        id: `slow-step-${t.id}-${slowSpan.id}`,
        severity: slowSpan.duration_ms > 10000 ? "error" : "warning",
        type: "high_latency",
        message: `[${t.id.slice(0, 8)}] ${slowSpan.name} 耗时 ${slowSpan.duration_ms}ms（>3s），可能影响整体响应`,
        trace_ids: [t.id],
        created_at: t.timestamp,
      });
    }
  });

  // 5) Rerank 零结果
  const rerankZero = traces.filter((t) => {
    const spans = t.spans || [];
    const rs = spans.find((s) => s.id === "rerank" || s.type === "rerank");
    return rs && Number(rs.metrics?.output_docs ?? -1) === 0;
  });
  if (rerankZero.length > 0) {
    alerts.push({
      id: "rerank-zero-results",
      severity: "warning",
      type: "high_latency",
      message: `${rerankZero.length} 条 trace 的 Rerank 过滤掉全部文档，回答可能脱离知识库`,
      trace_ids: rerankZero.map((t) => t.id),
      created_at: rerankZero[0].timestamp,
    });
  }

  // 按时间倒序
  return alerts.sort((a, b) => b.created_at.localeCompare(a.created_at));
}