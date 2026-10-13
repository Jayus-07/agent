import type { TraceRecord, TraceStatus } from "@/types/trace";

export type RunStatus = Exclude<TraceStatus, "all">;
export type BusinessOutcome = "failed" | "degraded" | "partial";

const SAFE_RUNTIME_LABEL = /^[\p{L}\p{N}][\p{L}\p{N}._:/-]{0,95}$/u;

/** 仅展示结构化诊断标识；自由文本可能包含用户或业务输入。 */
export function safeRuntimeLabel(value: unknown, fallback = "未记录"): string {
  if (typeof value !== "string") return fallback;
  const label = value.trim();
  return SAFE_RUNTIME_LABEL.test(label) ? label : fallback;
}

/** 错误码若不是短标识，则将其视作可能包含消息正文并隐藏。 */
export function safeDiagnosticCode(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const code = value.trim();
  return SAFE_RUNTIME_LABEL.test(code) ? code : null;
}

const RUN_STATUSES: readonly RunStatus[] = [
  "success",
  "failed",
  "error",
  "degraded",
  "partial",
  "timeout",
  "cancelled",
  "rejected",
  "running",
  "unknown",
];

export const RUN_STATUS_LABELS: Record<RunStatus, string> = {
  success: "成功",
  failed: "业务失败",
  error: "执行错误",
  degraded: "降级完成",
  partial: "部分成功",
  timeout: "超时",
  cancelled: "已取消",
  rejected: "已拒绝",
  running: "运行中",
  unknown: "状态未知",
};

export function getRunStatus(trace: Pick<TraceRecord, "status" | "error" | "metadata">): RunStatus {
  const status = String(trace.status ?? "").trim().toLowerCase();
  if (RUN_STATUSES.includes(status as RunStatus) && status !== "unknown") {
    // 某些旧记录根状态为 success，但显式业务结果为 failed/degraded。
    // 在列表和总览中优先呈现失败业务结果，避免用户把它当成成功运行。
    if (status === "success") {
      const outcome = trace.metadata?.business_outcome;
      if (outcome === "failed" || outcome === "degraded" || outcome === "partial") return outcome;
      // 兼容旧 Trace：DTO 可能把缺省状态填成 success，但实际记录了错误对象。
      // 不展示错误正文，只依据是否存在结构化错误证据纠正状态。
      if (trace.error && Object.keys(trace.error).length > 0) return "error";
    }
    return status as RunStatus;
  }

  // 旧 DTO 偶尔没有顶层 status；明确错误仍可标为错误，缺少证据时不推断成功。
  if (!status && trace.error && Object.keys(trace.error).length > 0) return "error";
  return "unknown";
}

export function getBusinessOutcome(trace: Pick<TraceRecord, "metadata">): BusinessOutcome | null {
  const outcome = trace.metadata?.business_outcome;
  return outcome === "failed" || outcome === "degraded" || outcome === "partial"
    ? outcome
    : null;
}

export function getRunStatusLabel(status: RunStatus): string {
  return RUN_STATUS_LABELS[status];
}

export function getRunStatusTone(status: RunStatus): string {
  switch (status) {
    case "success": return "border-emerald-200 bg-emerald-50 text-emerald-800";
    case "failed":
    case "error": return "border-red-200 bg-red-50 text-red-800";
    case "degraded":
    case "partial":
    case "timeout": return "border-amber-200 bg-amber-50 text-amber-800";
    case "rejected": return "border-orange-200 bg-orange-50 text-orange-800";
    case "cancelled": return "border-slate-200 bg-slate-100 text-slate-700";
    case "running": return "border-blue-200 bg-blue-50 text-blue-800";
    case "unknown": return "border-slate-200 bg-white text-slate-600";
  }
}

export type RuntimeWindow = "15m" | "1h" | "6h" | "24h";

export const RUNTIME_WINDOW_LABELS: Record<RuntimeWindow, string> = {
  "15m": "近 15 分钟",
  "1h": "近 1 小时",
  "6h": "近 6 小时",
  "24h": "近 24 小时",
};

const WINDOW_MS: Record<RuntimeWindow, number> = {
  "15m": 15 * 60 * 1000,
  "1h": 60 * 60 * 1000,
  "6h": 6 * 60 * 60 * 1000,
  "24h": 24 * 60 * 60 * 1000,
};

export function isInRuntimeWindow(
  trace: Pick<TraceRecord, "timestamp">,
  window: RuntimeWindow,
  nowMs = Date.now(),
): boolean {
  const timestamp = Date.parse(trace.timestamp || "");
  return Number.isFinite(timestamp) && timestamp <= nowMs && nowMs - timestamp <= WINDOW_MS[window];
}

export function percentile(values: number[], percentileValue: number): number | null {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (sorted.length === 0) return null;
  const index = Math.max(0, Math.ceil(sorted.length * percentileValue) - 1);
  return sorted[index];
}

export interface RuntimeSampleSummary {
  total: number;
  counts: Record<RunStatus, number>;
  terminalCount: number;
  successRate: number | null;
  p50DurationMs: number | null;
  p95DurationMs: number | null;
  costCny: number | null;
  costRecordCount: number;
  businessOutcomeCount: number;
  degradedCount: number;
}

export function summarizeRuntimeSample(
  traces: TraceRecord[],
  window: RuntimeWindow,
  nowMs = Date.now(),
): RuntimeSampleSummary {
  const inWindow = traces.filter((trace) => isInRuntimeWindow(trace, window, nowMs));
  const counts = Object.fromEntries(RUN_STATUSES.map((status) => [status, 0])) as Record<RunStatus, number>;
  inWindow.forEach((trace) => { counts[getRunStatus(trace)] += 1; });

  const terminalCount = inWindow.filter((trace) => {
    const status = getRunStatus(trace);
    return status !== "running" && status !== "unknown";
  }).length;
  const durations = inWindow
    .filter((trace) => !["running", "unknown"].includes(getRunStatus(trace)) && trace.duration_ms > 0)
    .map((trace) => trace.duration_ms);
  const costRecords = inWindow.filter((trace) => typeof trace.cost_cny === "number");
  const businessOutcomes = inWindow.filter((trace) => getBusinessOutcome(trace) !== null);

  return {
    total: inWindow.length,
    counts,
    terminalCount,
    successRate: terminalCount > 0 ? counts.success / terminalCount : null,
    p50DurationMs: percentile(durations, 0.5),
    p95DurationMs: percentile(durations, 0.95),
    costCny: costRecords.length > 0
      ? costRecords.reduce((totalCost, trace) => totalCost + (trace.cost_cny ?? 0), 0)
      : null,
    costRecordCount: costRecords.length,
    businessOutcomeCount: businessOutcomes.length,
    degradedCount: businessOutcomes.filter((trace) => getBusinessOutcome(trace) === "degraded").length,
  };
}

export function countRetryEvents(trace: Pick<TraceRecord, "error" | "spans">): number | null {
  const errorCount = trace.error?.retry_count;
  if (typeof errorCount === "number" && Number.isFinite(errorCount)) return errorCount;
  const events = trace.spans.flatMap((span) => span.events || []);
  const retryEvents = events.filter((event) => String(event.name || "").startsWith("retry_"));
  return retryEvents.length > 0 ? retryEvents.length : null;
}
