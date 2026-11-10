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

  it("透传 user-agent/cookie，并用 rl_client Cookie 作为限流键（不信 XFF）", async () => {
    const req = makeReq("/api/auth/login", {
      "content-type": "application/json",
      "user-agent": "Mozilla/5.0 TestBrowser/1.0",
      cookie: "refresh_token=old; Path=/api/auth",
      "x-forwarded-for": "203.0.113.9, 10.0.0.2",
      "x-real-ip": "203.0.113.8",
      "x-client-ip": "198.51.100.44",
    });
    const ctx = { params: Promise.resolve({ path: ["auth", "login"] }) };
    const res = await GET(req, ctx);

    expect(res.status).toBe(200);
    const upstreamHeaders = fetchMock.mock.calls[0][1].headers as Headers;
    expect(upstreamHeaders.get("user-agent")).toBe("Mozilla/5.0 TestBrowser/1.0");
    expect(upstreamHeaders.get("cookie")).toBe("refresh_token=old; Path=/api/auth");
    // 默认不采信客户端可伪造的 XFF/X-Real-IP
    expect(upstreamHeaders.get("x-client-ip")).toMatch(/^rl-[0-9a-f-]{36}$/);
    expect(upstreamHeaders.get("x-client-ip")).not.toContain("203.0.113");
    expect(upstreamHeaders.get("x-client-app")).toBe("admin");
    // 服务端签发 rl_client Cookie
    const issued = res.headers.getSetCookie().find((c) => c.startsWith("rl_client="));
    expect(issued).toBeTruthy();
    expect(issued).toContain("HttpOnly");
  });

  it("已有 rl_client Cookie 时复用同一限流键", async () => {
    const id = "11111111-2222-3333-4444-555555555555";
    const req = makeReq("/api/auth/login", {
      "content-type": "application/json",
      cookie: `rl_client=${id}`,
      "x-forwarded-for": "203.0.113.9",
    });
    const ctx = { params: Promise.resolve({ path: ["auth", "login"] }) };
    const res = await GET(req, ctx);

    const upstreamHeaders = fetchMock.mock.calls[0][1].headers as Headers;
    expect(upstreamHeaders.get("x-client-ip")).toBe(`rl-${id}`);
    expect(
      res.headers.getSetCookie().some((c) => c.startsWith("rl_client=")),
    ).toBe(false);
  });

  it("显式 TRUST_PROXY_HEADERS=true 时才采信 XFF 首段", async () => {
    vi.stubEnv("TRUST_PROXY_HEADERS", "true");
    try {
      const req = makeReq("/api/auth/login", {
        "content-type": "application/json",
        "x-forwarded-for": "203.0.113.9, 10.0.0.2",
      });
      const ctx = { params: Promise.resolve({ path: ["auth", "login"] }) };
      await GET(req, ctx);
      const upstreamHeaders = fetchMock.mock.calls[0][1].headers as Headers;
      expect(upstreamHeaders.get("x-client-ip")).toBe("203.0.113.9");
    } finally {
      vi.unstubAllEnvs();
    }
  });

  it("透传 Idempotency-Key，治理写接口才能通过后端幂等门", async () => {
    const req = makeReq("/api/sys/model-roles/fallback", {
      "content-type": "application/json",
      "idempotency-key": "fallback-change-1",
      authorization: "Bearer test-token",
    });
    const ctx = { params: Promise.resolve({ path: ["sys", "model-roles", "fallback"] }) };
    await GET(req, ctx);

    const upstreamHeaders = fetchMock.mock.calls[0][1].headers as Headers;
    expect(upstreamHeaders.get("idempotency-key")).toBe("fallback-change-1");
  });

  it("透传 X-Batch-Id，批量删除/重索引/上传的批次关联才不失效（2026-11-10 评审回归）", async () => {
    const req = makeReq("/api/rag/documents/delete", {
      "content-type": "application/json",
      authorization: "Bearer test-token",
      "x-batch-id": "batch-admin-1",
    });
    const ctx = { params: Promise.resolve({ path: ["rag", "documents", "delete"] }) };
    await GET(req, ctx);

    const upstreamHeaders = fetchMock.mock.calls[0][1].headers as Headers;
    expect(upstreamHeaders.get("x-batch-id")).toBe("batch-admin-1");
  });

  it("RAG 上传超路径上限（50MiB+16KiB）在 BFF 直接 413 人话拒绝，不触达上游（2026-11-10 冒烟修复同步）", async () => {
    const req = makeReq("/api/rag/upload", {
      "content-type": "multipart/form-data; boundary=x",
      "content-length": String(50 * 1024 * 1024 + 16 * 1024 + 1),
      authorization: "Bearer test-token",
    });
    const ctx = { params: Promise.resolve({ path: ["rag", "upload"] }) };
    const res = await GET(req, ctx);

    expect(res.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
    const body = (await res.json()) as { error?: string; detail?: string };
    expect(body.error).toBe("PayloadTooLarge");
    // detail 人话对齐后端 body_limit 413 字段结构（前端解析链 detail 优先）
    expect(body.detail).toContain("请求体超过上限");
  });

  it("响应侧逐条透传 set-cookie（refresh_token Cookie 生命周期依赖）", async () => {
    const req = makeReq("/api/auth/login", { "content-type": "application/json" });
    const ctx = { params: Promise.resolve({ path: ["auth", "login"] }) };
    const res = await GET(req, ctx);

    const cookies = res.headers.getSetCookie();
    expect(cookies.length).toBeGreaterThanOrEqual(1);
    expect(cookies.some((c) => c.startsWith("refresh_token=abc"))).toBe(true);
  });

  it("私有路由无 Bearer 时本地拒绝", async () => {
    const req = makeReq("/api/sys/model-roles/fallback", {}, "GET");
    const ctx = { params: Promise.resolve({ path: ["sys", "model-roles", "fallback"] }) };
    const res = await GET(req, ctx);

    expect(res.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
