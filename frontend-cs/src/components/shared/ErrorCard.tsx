"use client";

import { useState } from "react";
import { describeApiError, type ResolvedError } from "@/api/errors";

const KIND_ACTIONS: Record<string, string> = {
  auth: "登录状态已失效：请重新登录",
  permission: "当前角色无权执行该操作：请联系管理员",
  validation: "提交内容未通过校验：请检查输入",
  not_found: "资源不存在或已移动：请刷新列表",
  conflict: "数据已被他人修改：请刷新后重试",
  rate_limit: "操作过于频繁：请稍候片刻再试",
  server: "服务暂时不可用：请稍后重试",
  network: "无法连接服务器：请检查网络",
  timeout: "响应超时：请稍后重试",
  canceled: "操作已取消：无需处理",
  unknown: "操作失败：请稍后重试或联系管理员",
};

function actionText(error: ResolvedError): string {
  if (error.code === "BUDGET_EXCEEDED") {
    const scope = String(error.details?.budget_kind ?? "预算");
    const period = String(error.details?.limit_kind ?? error.details?.period_type ?? "额度");
    const reset = typeof error.details?.reset_at === "string" ? `，预计 ${formatTime(error.details.reset_at)} 恢复` : "";
    return `已达到${scope}${period}${reset}，请查看预算策略`;
  }
  return KIND_ACTIONS[error.kind] ?? KIND_ACTIONS.unknown;
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", dateStyle: "medium", timeStyle: "short" }).format(date);
}

export default function ErrorCard({
  error,
  onRetry,
  onHandoff,
  actionsDisabled = false,
}: {
  error: unknown;
  onRetry?: () => void;
  onHandoff?: () => void;
  actionsDisabled?: boolean;
}) {
  const [showDetails, setShowDetails] = useState(false);
  const resolved = describeApiError(error);
  return (
    <div role="alert" className="w-full rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <p className="text-sm font-medium text-slate-900">{resolved.message}</p>
      <p className="mt-2 rounded-lg bg-slate-50 px-3 py-2 text-xs leading-relaxed text-slate-600">
        {actionText(resolved)}
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        {!actionsDisabled && resolved.retryable && onRetry && (
          <button type="button" onClick={onRetry} className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover">重试</button>
        )}
        {!actionsDisabled && resolved.handoffAvailable && (
          <button type="button" onClick={onHandoff ?? (() => { window.location.assign("/agent?handoff=1"); })} className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-800">转人工</button>
        )}
        {resolved.traceId && (
          <button type="button" onClick={() => { void navigator.clipboard?.writeText(resolved.traceId ?? ""); }} className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600">复制 Trace ID</button>
        )}
      </div>
      <button type="button" onClick={() => setShowDetails((value) => !value)} className="mt-3 text-xs text-slate-400 hover:text-slate-600" aria-expanded={showDetails}>
        {showDetails ? "收起详情 ▲" : "查看详情 ▼"}
      </button>
      {showDetails && (
        <div className="mt-2 space-y-1 rounded-lg border border-slate-100 bg-slate-50 p-3 font-mono text-[11px] text-slate-500">
          {resolved.code && <p>code: {resolved.code}</p>}
          {resolved.status !== undefined && <p>http_status: {resolved.status}</p>}
          <p>retryable: {String(resolved.retryable)}</p>
          {resolved.traceId && <p>trace_id: {resolved.traceId}</p>}
        </div>
      )}
    </div>
  );
}
