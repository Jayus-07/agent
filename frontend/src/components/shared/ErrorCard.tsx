"use client";

/**
 * ErrorCard — 三段式错误卡片（UX 设计 P0-②，§4.4 反馈语义层）
 *
 * 三段式：发生了什么（已中文化文案 + 码/状态）/ 我能做什么（行动指引）/
 * 详情（折叠：码、状态、原始错误摘要——不渲染原始报错正文）。
 *
 * 数据来源：`describeApiError`（src/api/errors.ts 的降级链，永不抛错）。
 * 行动指引按「RAG 拒答语义码 → ErrorKind」两级映射：
 * - RAG_NO_EVIDENCE / RAG_PERMISSION_DENIED / RAG_VERSION_CONFLICT：
 *   后端 C4 错误码接通后（响应体携带 code 字段）**无需改本组件即点亮**；
 *   接通前这三个码不会被后端发送，映射只是预登记的契约（不写假入口——
 *   权限申请页/版本历史页当前不存在，故只给文案不带跳转）。
 * - HTTP 401/403/429/超时/网络等已由 errors.ts 既有机制覆盖（FALLBACK_BY_STATUS
 *   + CLIENT_ERRORS，有测试锁定），本卡片按 kind 给行动指引。
 *
 * 与 ErrorState 的关系：ErrorState 是既有简单错误态（title/message/retry），
 * 本卡片是「有行动指引」的升级版，供新接线逐步采用；不强拆旧组件。
 */

import { useState } from "react";
import { describeApiError, type ResolvedError } from "@/api/errors";
import { getIdempotencyOperationStatus, type IdempotencyOperationSummary } from "@/api/idempotency";

/** RAG 拒答语义码 → 行动指引（预登记契约，后端 C4 接通前不命中） */
export const RAG_REJECTION_ACTIONS: Record<string, string> = {
  RAG_NO_EVIDENCE: "知识库中未找到依据：换个问法重试，或补充更多背景信息",
  RAG_PERMISSION_DENIED: "该知识对当前角色受限：申请访问权限或联系管理员",
  RAG_VERSION_CONFLICT: "命中多版本/过期版本制度：请确认制度版本或查看历史版本",
};

/** 按 ErrorKind 给行动指引（401/403/429/超时/网络等，errors.ts 既有机制的呈现层） */
export const KIND_ACTIONS: Record<string, string> = {
  auth: "登录状态已失效：请重新登录",
  permission: "当前角色无权执行该操作：请联系管理员开通",
  validation: "提交内容未通过校验：请检查输入后重试",
  not_found: "内容可能已被删除或移动：请确认后刷新列表",
  conflict: "数据已被他人修改：请刷新后重试",
  rate_limit: "操作过于频繁：请稍候片刻再试",
  server: "服务暂时不可用：请稍后重试",
  network: "无法连接服务器：请检查网络后重试",
  timeout: "响应超时：请检查网络后重试",
  canceled: "操作已取消：无需处理",
  unknown: "操作失败：请稍后重试或联系管理员",
};

const BUDGET_SCOPE_LABELS: Record<string, string> = {
  request: "本次请求预算",
  user: "用户",
  tenant: "租户",
  price: "价格覆盖",
};

const BUDGET_PERIOD_LABELS: Record<string, string> = {
  daily: "日额度",
  day: "日额度",
  monthly: "月额度",
  month: "月额度",
};

function budgetActionText(resolved: ResolvedError): string {
  const scope = resolved.details?.budget_kind;
  const period = resolved.details?.limit_kind ?? resolved.details?.period_type;
  if (typeof scope !== "string") return "预算条件未满足：请查看预算状态或联系管理员";
  if (scope === "request") return "本次请求预算已用尽：减少任务范围后重新发起";
  const scopeLabel = BUDGET_SCOPE_LABELS[scope] ?? "预算";
  const periodLabel = typeof period === "string" ? (BUDGET_PERIOD_LABELS[period] ?? period) : "额度";
  const resetAt = resolved.details?.reset_at;
  const resetText = typeof resetAt === "string" ? `，预计 ${formatTime(resetAt)} 恢复` : "，请联系管理员查看策略";
  return `已达到${scopeLabel}${periodLabel}${resetText}`;
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export interface ErrorFeedback extends ResolvedError {
  /** 行动指引文案（RAG 语义码优先于 kind 映射） */
  actionText: string;
}

/** 纯函数：任意异常 → 三段式反馈（永不抛错，异常安全性继承自 describeApiError） */
export function resolveErrorFeedback(err: unknown): ErrorFeedback {
  const resolved = describeApiError(err);
  const ragAction =
    typeof resolved.code === "string" ? RAG_REJECTION_ACTIONS[resolved.code] : undefined;
  return {
    ...resolved,
    actionText: ragAction
      ?? (resolved.code === "BUDGET_EXCEEDED" ? budgetActionText(resolved) : undefined)
      ?? KIND_ACTIONS[resolved.kind]
      ?? KIND_ACTIONS.unknown,
  };
}

/** 从原始错误安全提取一行摘要（详情折叠区用，不抛错） */
function causeSummary(cause: unknown): string | null {
  if (cause == null) return null;
  if (cause instanceof Error) return cause.message || cause.name;
  if (typeof cause === "string") return cause;
  return null;
}

export default function ErrorCard({
  error,
  /** 已解析好的反馈（可选，便于调用方复用同一次 resolve） */
  feedback,
  onRetry,
  onHandoff,
  actionsDisabled = false,
  className = "",
}: {
  error?: unknown;
  feedback?: ErrorFeedback;
  /** 重试回调；仅在反馈标记 retriable 时渲染 */
  onRetry?: () => void;
  /** 后端明确允许转人工时显示；默认进入客服入口。 */
  onHandoff?: () => void;
  /** 硬额度下隐藏会发起模型/写操作的错误动作；只读详情仍保留。 */
  actionsDisabled?: boolean;
  className?: string;
}) {
  const [openDetail, setOpenDetail] = useState(false);
  const [operationStatus, setOperationStatus] = useState<IdempotencyOperationSummary | null>(null);
  const [operationLoading, setOperationLoading] = useState(false);
  const [operationError, setOperationError] = useState("");
  const fb = feedback ?? resolveErrorFeedback(error);
  const summary = causeSummary(fb.cause);

  const viewOperationStatus = async () => {
    if (!fb.idempotencyKey || operationLoading) return;
    setOperationLoading(true);
    setOperationError("");
    try {
      setOperationStatus(await getIdempotencyOperationStatus(fb.idempotencyKey));
    } catch {
      setOperationError("原操作状态暂时无法查询，请稍后刷新重试");
    } finally {
      setOperationLoading(false);
    }
  };

  return (
    <div
      role="alert"
      className={`w-full max-w-md rounded-xl border border-gray-200 bg-white p-4 shadow-card ${className}`}
    >
      <p className="text-sm font-medium text-gray-900">{fb.message}</p>

      <div className="mt-2 rounded-lg bg-gray-50 px-3 py-2">
        <p className="text-xs leading-relaxed text-gray-600">
          <span className="font-medium text-gray-700">我能做什么：</span>
          {fb.actionText}
        </p>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
      {!actionsDisabled && fb.retryable && onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover"
        >
          重试
        </button>
      )}

      {!actionsDisabled && fb.handoffAvailable && (
        <button
          type="button"
          onClick={onHandoff ?? (() => { window.location.assign("/agent?handoff=1"); })}
          className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-800 hover:bg-amber-100"
        >
          转人工
        </button>
      )}

      {fb.traceId && (
        <button
          type="button"
          onClick={() => { void navigator.clipboard?.writeText(fb.traceId ?? ""); }}
          className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50"
        >
          复制 Trace ID
        </button>
      )}
      {fb.code === "IDEMPOTENCY_CONFLICT" && fb.idempotencyKey && (
        <button
          type="button"
          onClick={() => { void viewOperationStatus(); }}
          disabled={operationLoading}
          className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs text-blue-700 hover:bg-blue-100 disabled:opacity-50"
        >
          {operationLoading ? "查询中…" : "查看原操作状态"}
        </button>
      )}
      </div>

      {operationStatus && (
        <p className="mt-2 rounded-lg bg-blue-50 px-3 py-2 text-xs text-blue-800">
          原操作状态：{operationStatus.status === "running" ? "处理中" : operationStatus.status === "succeeded" ? "已完成" : operationStatus.status === "uncertain" ? "结果待确认" : "失败"}
        </p>
      )}
      {operationError && <p className="mt-2 text-xs text-red-600">{operationError}</p>}

      <button
        type="button"
        onClick={() => setOpenDetail((v) => !v)}
        className="mt-3 block text-xs text-gray-400 hover:text-gray-600"
        aria-expanded={openDetail}
      >
        {openDetail ? "收起详情 ▲" : "查看详情 ▼"}
      </button>
      {openDetail && (
        <div className="mt-2 space-y-1 rounded-lg border border-gray-100 bg-gray-50 p-3 font-mono text-[11px] leading-relaxed text-gray-500">
          {fb.code && <p>code: {fb.code}</p>}
          {fb.status !== undefined && <p>http_status: {fb.status}</p>}
          <p>kind: {fb.kind} · retryable: {String(fb.retryable)}</p>
          {fb.traceId && <p>trace_id: {fb.traceId}</p>}
          {fb.source && <p>source: {fb.source}</p>}
          {summary && <p>错误详情已隐藏，便于保护内部实现信息</p>}
        </div>
      )}
    </div>
  );
}
