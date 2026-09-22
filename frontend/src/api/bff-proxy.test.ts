/**
 * BFF 代理（app/api/[...path]/route.ts）转发头契约测试（2026-09-23 缺陷4）
 *
 * 背景：脚本验收直连网关，真实 UI 经 Next BFF 转发——白名单缺
 * `idempotency-key` 时幂等键被剥，后端 400「Idempotency-Key 必须为
 * 1-128 个字符」，用户端「转人工」全挂。本文件把「BFF 不丢业务头」
 * 锁进回归：请求侧白名单逐项断言 + 服务端凭据注入 + GET 透传。
 *
 * 放在 src/api/ 而非 [...path] 目录：目录名里的方括号会被 vitest 的
 * glob include 当字符类解释，测试文件放那里根本不会被收集。
 * route.ts 对 NextRequest 是 type-only import，运行时不需要 Next 环境，
 * 用 Request + nextUrl/ip 两个运行时字段补齐 proxy 的实际消费面即可。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import { GET, POST } from "../app/api/[...path]/route";

type ProxyRequest = Parameters<typeof POST>[0];

function makeProxyRequest(
  url: string,
  init: RequestInit,
): ProxyRequest {
  const req = Object.assign(new Request(url, init), {
    nextUrl: new URL(url),
    ip: "127.0.0.1",
  });
  return req as unknown as ProxyRequest;
}

const ctx = (segments: string[]) => ({ params: { path: segments } });

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const upstream = () =>
  vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ ok: true }));

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("BFF 转发头白名单（缺陷4：幂等键不丢）", () => {
  it("转人工 POST 的 Idempotency-Key 必须原样到达上游", async () => {
    const fetchSpy = upstream();

    await POST(
      makeProxyRequest(
        "http://localhost:3000/api/cs/conversations/conv-1/handoff",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": "idem-abc-123",
            Authorization: "Bearer user-token",
          },
          body: JSON.stringify({}),
        },
      ),
      ctx(["cs", "conversations", "conv-1", "handoff"]),
    );

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [target, init] = fetchSpy.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(target).toContain("/api/cs/conversations/conv-1/handoff");
    const sent = new Headers(init.headers);
    expect(sent.get("idempotency-key")).toBe("idem-abc-123");
  });

  it("小写 idempotency-key 请求头同样转发（Headers 取值本就大小写不敏感）", async () => {
    const fetchSpy = upstream();

    await POST(
      makeProxyRequest("http://localhost:3000/api/x", {
        method: "POST",
        headers: { "idempotency-key": "idem-lower-1" },
        body: "{}",
      }),
      ctx(["x"]),
    );

    const sent = new Headers((fetchSpy.mock.calls[0] as unknown as [string, RequestInit])[1].headers);
    expect(sent.get("idempotency-key")).toBe("idem-lower-1");
  });

  it("白名单既有头不回归：Authorization / Content-Type / X-Trace-Id 照常转发", async () => {
    const fetchSpy = upstream();

    await POST(
      makeProxyRequest("http://localhost:3000/api/x", {
        method: "POST",
        headers: {
          Authorization: "Bearer t",
          "Content-Type": "application/json",
          "X-Trace-Id": "trace-1",
        },
        body: "{}",
      }),
      ctx(["x"]),
    );

    const sent = new Headers((fetchSpy.mock.calls[0] as unknown as [string, RequestInit])[1].headers);
    expect(sent.get("authorization")).toBe("Bearer t");
    expect(sent.get("content-type")).toBe("application/json");
    expect(sent.get("x-trace-id")).toBe("trace-1");
    // Cookie 不在此模拟：Node fetch（undici）按规范把 Cookie 列为 forbidden
    // request header，测试环境构造 Request 时就会被剥；真实链路的 Cookie 由
    // 浏览器 cookie jar 发出、Next 服务器端可读，BFF 白名单透传不受影响。
  });

  it("服务端凭据收口：X-API-Key 由 BFF 注入，非白名单头（如 X-Evil）被剥", async () => {
    const fetchSpy = upstream();
    process.env.API_KEY = "server-only-key";

    try {
      await POST(
        makeProxyRequest("http://localhost:3000/api/x", {
          method: "POST",
          headers: { "X-Evil": "nope" },
          body: "{}",
        }),
        ctx(["x"]),
      );
    } finally {
      delete process.env.API_KEY;
    }

    const sent = new Headers((fetchSpy.mock.calls[0] as unknown as [string, RequestInit])[1].headers);
    expect(sent.get("x-api-key")).toBe("server-only-key");
    expect(sent.get("x-evil")).toBeNull();
  });

  it("GET 请求透传 query 且不携带 body", async () => {
    const fetchSpy = upstream();

    await GET(
      makeProxyRequest(
        "http://localhost:3000/api/cs/conversations/conv-1/messages?since_id=3",
        { method: "GET", headers: { Authorization: "Bearer t" } },
      ),
      ctx(["cs", "conversations", "conv-1", "messages"]),
    );

    const [target, init] = fetchSpy.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(target).toContain("/api/cs/conversations/conv-1/messages?since_id=3");
    expect(init.body).toBeUndefined();
  });
});
