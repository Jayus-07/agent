// @vitest-environment node
/**
 * route.test.ts — BFF 代理转发回归（2026-09-19）
 *
 * 背景：代理白名单原本不含 user-agent/cookie/set-cookie，导致后端会话台账
 * 记到 "node" UA 与代理层 IP、refresh_token HttpOnly Cookie 永远到不了浏览器
 * （静默刷新必失败，token 过期后被迫重新登录）。本测试锁住转发契约：
 *   请求侧：user-agent / cookie 透传 + X-Client-IP 注入
 *   响应侧：set-cookie 逐条透传
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { NextRequest } from "next/server";

const { GET } = await import("./route");

function makeReq(path: string, headers: Record<string, string>, method = "POST") {
  return new NextRequest(`http://localhost:3200${path}`, {
    method,
    headers,
    body: method === "GET" ? undefined : "{}",
  });
}

describe("BFF 代理转发契约", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ code: 200 }), {
        status: 200,
        headers: {
          "content-type": "application/json",
          // 模拟后端登录响应的两条 Set-Cookie（多条是轮换场景的常态）
          "set-cookie": "refresh_token=abc; Path=/api/auth; HttpOnly",
        },
      }),
    );
  });

  it("透传 user-agent 与 cookie，并注入 X-Client-IP", async () => {
    const req = makeReq("/api/auth/login", {
      "content-type": "application/json",
      "user-agent": "Mozilla/5.0 TestBrowser/1.0",
      cookie: "refresh_token=old; Path=/api/auth",
      "x-forwarded-for": "203.0.113.9, 10.0.0.2",
    });
    const ctx = { params: { path: ["auth", "login"] } };
    const res = await GET(req as never, ctx as never);

    expect(res.status).toBe(200);
    const upstreamHeaders = fetchMock.mock.calls[0][1].headers as Headers;
    expect(upstreamHeaders.get("user-agent")).toBe("Mozilla/5.0 TestBrowser/1.0");
    expect(upstreamHeaders.get("cookie")).toBe("refresh_token=old; Path=/api/auth");
    expect(upstreamHeaders.get("x-client-ip")).toBe("203.0.113.9");
  });

  it("透传 Idempotency-Key，治理写接口才能通过后端幂等门", async () => {
    const req = makeReq("/api/sys/model-roles/fallback", {
      "content-type": "application/json",
      "idempotency-key": "fallback-change-1",
      authorization: "Bearer test-token",
    });
    const ctx = { params: { path: ["sys", "model-roles", "fallback"] } };
    await GET(req as never, ctx as never);

    const upstreamHeaders = fetchMock.mock.calls[0][1].headers as Headers;
    expect(upstreamHeaders.get("idempotency-key")).toBe("fallback-change-1");
  });

  it("无 XFF 时回退 req.ip 作为 X-Client-IP", async () => {
    const req = makeReq("/api/auth/login", { "content-type": "application/json" });
    Object.defineProperty(req, "ip", { value: "127.0.0.1" });
    const ctx = { params: { path: ["auth", "login"] } };
    await GET(req as never, ctx as never);

    const upstreamHeaders = fetchMock.mock.calls[0][1].headers as Headers;
    expect(upstreamHeaders.get("x-client-ip")).toBe("127.0.0.1");
  });

  it("响应侧逐条透传 set-cookie（refresh_token Cookie 生命周期依赖）", async () => {
    const req = makeReq("/api/auth/login", { "content-type": "application/json" });
    const ctx = { params: { path: ["auth", "login"] } };
    const res = await GET(req as never, ctx as never);

    const cookies = res.headers.getSetCookie();
    expect(cookies.length).toBeGreaterThanOrEqual(1);
    expect(cookies.some((c) => c.startsWith("refresh_token=abc"))).toBe(true);
  });
});
