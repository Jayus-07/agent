/**
 * Chat 业务 API：流式对话 + 中止
 */
import { request, requestSilent } from "@/lib/fetcher";
import { apiErrorFromEnvelope } from "@/api/client";
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
  /** 当前逻辑发送操作的幂等键；重试时保持不变。 */
  idempotency_key?: string;
  /** 会话级模型覆盖（B.9 决策②）：仅本次请求生效，不改动全局默认。
   *  后端 API 边界会校验（未注册 / provider Key 缺失 → 400 fail-fast），
   *  非法值不会被静默吞掉再跑全局模型。 */
  model?: string;
}

/**
 * 复用 lib/types 的具体联合类型（meta/status/log/delta/done/error）
 * 这样 store/chat 等已有消费方不需要改类型签名
 */
export type SSEStreamEvent = TypedSSEStreamEvent;

/** F2 SSE Resume：断线后最多续播尝试次数（与后端 SSE_RESUME_MAX_ATTEMPTS 同口径） */
const MAX_RESUME_ATTEMPTS = 3;

function seqOf(evt: SSEStreamEvent): number | null {
  const d = evt.data as { seq?: unknown };
  return typeof d?.seq === "number" ? d.seq : null;
}

/**
 * POST /chat/stream — 流式对话（F2 可恢复）
 *
 * 传输 at-least-once：断流（非 done/error 的意外终止）自动调
 * /chat/stream/resume 从 last seq 续播，按 seq 去重 → UI effectively-once。
 * 404 STREAM_NOT_RESUMABLE（进程重启/超出缓冲窗口）如实上抛，由用户重发。
 */
export async function* streamChat(
  req: ChatRequest,
  signal?: AbortSignal,
): AsyncGenerator<SSEStreamEvent> {
  const doFetch = (path: string, body: string) =>
    fetch(`${process.env.NEXT_PUBLIC_API_URL || ""}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": req.idempotency_key || req.request_id,
        // 凭据收口（2026-09-16 方案 B）：X-API-Key 由 BFF 代理路由
        // （app/api/[...path]/route.ts）服务端注入。勿引用 NEXT_PUBLIC_API_KEY ——
        // NEXT_PUBLIC_* 会被 Next 内联进浏览器 bundle，等于重新泄漏服务级密钥。
        ...bearerHeaders(),
      },
      body,
      signal,
    });

  let attempt = 0;
  let afterSeq = 0;
  let sawTerminal = false;
  // 流式内容本体只发一次：重试仅走 resume（重放+续播），绝不重发提问
  const askBody = JSON.stringify(req);
  const resumeBodyFor = () => JSON.stringify({
    request_id: req.request_id,
    after_seq: afterSeq,
  });

  while (true) {
    const path = attempt === 0 ? "/api/chat/stream" : "/api/chat/stream/resume";
    const body = attempt === 0 ? askBody : resumeBodyFor();
    let res = await doFetch(path, body);

    // 401：静默刷新一次并重试；刷新失败则清态跳登录页
    if (res.status === 401) {
      if (await tryRefreshOnce()) {
        res = await doFetch(path, body);
      } else {
        handleAuthFailure();
        throw apiErrorFromEnvelope({
          code: "PERMISSION_DENIED",
          message: "登录已过期",
          retryable: false,
        }, 401);
      }
    }

    if (!res.ok || !res.body) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      const protocol = err && typeof err === "object" && typeof err.code === "string"
        ? err
        : err?.detail && typeof err.detail === "object" ? err.detail : undefined;
      throw apiErrorFromEnvelope(protocol ?? err, res.status);
    }

    for await (const evt of parseSSEStream(res.body, signal) as AsyncGenerator<SSEStreamEvent>) {
      const seq = seqOf(evt);
      if (seq !== null) {
        if (seq <= afterSeq) continue; // 重放与 live 交叠：seq 去重
        afterSeq = seq;
      }
      if (evt.event === "done" || evt.event === "error") sawTerminal = true;
      yield evt;
    }

    if (sawTerminal || signal?.aborted) return;
    attempt += 1;
    if (attempt > MAX_RESUME_ATTEMPTS) {
      throw apiErrorFromEnvelope({
        code: "UPSTREAM_UNAVAILABLE",
        message: "连接多次中断且无法恢复，请重新发送。",
        retryable: true,
      }, 504);
    }
    // 指数退避后重连（1s/2s/3s 封顶）
    await new Promise((r) => setTimeout(r, Math.min(1000 * attempt, 3000)));
  }
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
