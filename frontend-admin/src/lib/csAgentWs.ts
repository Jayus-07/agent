"use client";

/**
 * useAgentSocket — 坐席工作台 WS 实时推送通道（2026-09-17 人工介入 v2）
 *
 * 握手链：HTTP 签发一次性 ticket（BFF 注入 X-API-Key，Key 不进浏览器）
 *        → APISIX /ws/cs/* → ws://后端/ws/cs/agent?ticket=xxx（P3.3 过网关）。
 * 断线：指数退避重连（1s 起，封顶 30s），重连自动换新 ticket。
 * 降级：connected=false 时页面回退 2s 轮询（transport fallback，不进业务逻辑）。
 */
import { useEffect, useRef, useState } from "react";
import { issueWsTicket } from "@/api/cs";

export type AgentEvent = {
  type: string;
  [key: string]: unknown;
};

// P3.3：WS 走 APISIX 网关 /ws/cs/*（此前硬编码 ws://127.0.0.1:8000 绕网关）。
// 优先 NEXT_PUBLIC_CS_WS_URL（如 ws://gateway:9080）；缺省推导同源 ——
// 网关与前端同域部署时零配置；不同域必须显式配置。同源不可达时
// useAgentSocket 自带退避重连 + 轮询降级兜底（不会白屏）。
const WS_BASE =
  process.env.NEXT_PUBLIC_CS_WS_URL ??
  (typeof window !== "undefined"
    ? `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}`
    : "");
const MAX_RETRY_DELAY_MS = 30_000;
const PING_INTERVAL_MS = 20_000;

export function useAgentSocket(onEvent: (e: AgentEvent) => void) {
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef(0);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    let disposed = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let pingTimer: ReturnType<typeof setInterval> | undefined;

    const connect = async () => {
      if (disposed) return;
      try {
        const { ticket, ws_path } = await issueWsTicket();
        const ws = new WebSocket(
          `${WS_BASE}${ws_path}?ticket=${encodeURIComponent(ticket)}`,
        );

        ws.onopen = () => {
          retryRef.current = 0;
          setConnected(true);
          pingTimer = setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) ws.send("ping");
          }, PING_INTERVAL_MS);
        };

        ws.onmessage = (ev) => {
          try {
            onEventRef.current(JSON.parse(ev.data as string) as AgentEvent);
          } catch {
            // 非 JSON 帧忽略
          }
        };

        ws.onclose = () => {
          setConnected(false);
          if (pingTimer) clearInterval(pingTimer);
          if (disposed) return;
          const delay = Math.min(
            MAX_RETRY_DELAY_MS,
            1000 * 2 ** retryRef.current++,
          );
          reconnectTimer = setTimeout(connect, delay);
        };

        wsRef.current = ws;
      } catch {
        // ticket 签发失败（后端暂不可用等）→ 退避后重试
        if (disposed) return;
        const delay = Math.min(
          MAX_RETRY_DELAY_MS,
          1000 * 2 ** retryRef.current++,
        );
        reconnectTimer = setTimeout(connect, delay);
      }
    };

    connect();

    return () => {
      disposed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (pingTimer) clearInterval(pingTimer);
      wsRef.current?.close();
    };
  }, []);

  return { connected };
}
