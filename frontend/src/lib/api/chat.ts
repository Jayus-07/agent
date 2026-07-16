/**
 * Chat 业务 API：流式对话 + 中止
 */
import { request, requestSilent } from "../fetcher";
import { parseSSEStream } from "../sse-parser";
import type { SSEStreamEvent as TypedSSEStreamEvent } from "../types";

export interface ChatRequest {
  question: string;
  session_id: string;
  request_id: string;
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
  const res = await fetch(
    `${process.env.NEXT_PUBLIC_API_URL || ""}/chat/stream`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
      signal,
    },
  );
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
  await requestSilent("/chat/abort", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, request_id: requestId }),
  });
}