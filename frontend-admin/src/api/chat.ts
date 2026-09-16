/**
 * Chat 业务 API：流式对话 + 中止
 */
import { request, requestSilent } from "@/lib/fetcher";
import { bearerHeaders, handleAuthFailure, tryRefreshOnce } from "@/lib/auth";
import { parseSSEStream } from "@/lib/sse-parser";
import type { SSEStreamEvent as TypedSSEStreamEvent } from "@/lib/types";

export interface ChatRequest {
  question: string;
  session_id: string;
  request_id: string;
  /** 员工部门ID（检索授权用）：带部门 = employee 主体，按部门矩阵授权；
   *  不带 = 后端按对客最严格集合处理（fail-safe） */
  department?: string;
}

/**
 * 复用 lib/types 的具体联合类型（meta/status/log/delta/done/error）
 * 这样 store/chat 等已有消费方不需要改类型签名
 */
export type SSEStreamEvent = TypedSSEStreamEvent;

/**
 * POST /chat/stream — 流式对话
 */
export async function* streamChat(
  req: ChatRequest,
  signal?: AbortSignal,
): AsyncGenerator<SSEStreamEvent> {
  const doFetch = () =>
    fetch(`${process.env.NEXT_PUBLIC_API_URL || ""}/api/chat/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        // 凭据收口（2026-09-16 方案 B）：X-API-Key 由 BFF 代理路由
        // （app/api/[...path]/route.ts）服务端注入。勿引用 NEXT_PUBLIC_API_KEY ——
        // NEXT_PUBLIC_* 会被 Next 内联进浏览器 bundle，等于重新泄漏服务级密钥。
        ...bearerHeaders(),
      },
      body: JSON.stringify(req),
      signal,
    });

  let res = await doFetch();

  // 401：静默刷新一次并重试；刷新失败则清态跳登录页
  if (res.status === 401) {
    if (await tryRefreshOnce()) {
      res = await doFetch();
    } else {
      handleAuthFailure();
      throw new Error("登录已过期");
    }
  }

  if (!res.ok || !res.body) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    const detail = err?.detail;
    const message =
      (typeof detail === "string" && detail) ||
      (typeof detail === "object" && detail?.error) ||
      `HTTP ${res.status}`;
    throw new Error(String(message));
  }

  yield* parseSSEStream(res.body, signal) as AsyncGenerator<SSEStreamEvent>;
}

/**
 * POST /chat/abort — 中止当前对话
 */
export async function abortChat(sessionId: string, requestId: string): Promise<void> {
  await requestSilent("/api/chat/abort", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, request_id: requestId }),
  });
}