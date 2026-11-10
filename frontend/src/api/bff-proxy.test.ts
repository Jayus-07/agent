// @vitest-environment node
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
 * 用 NextRequest 构造（与 admin/cs 同款）：plain Request 会按规范剥掉
 * 手设的 content-length（按实际 body 重算），超限预检用例需要保留声明头。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

import { GET, POST } from "../app/api/[...path]/route";

type ProxyRequest = Parameters<typeof POST>[0];

function makeProxyRequest(
  url: string,
  init: RequestInit,
): ProxyRequest {
  // NextRequest 的 init 是 Next 扩展的 RequestInit 类型（与 DOM 版结构兼容
  // 但 TS 互斥），经构造器参数类型收窄转换
  return new NextRequest(
    url,
    init as ConstructorParameters<typeof NextRequest>[1],
  ) as unknown as ProxyRequest;
}

// App Router 签名（Next 15）：params 是 Promise
const ctx = (segments: string[]) => ({
  params: Promise.resolve({ path: segments }),
});

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
  it("登录限流来源由 BFF 重建：默认用 rl_client Cookie，不信可伪造的 XFF/X-Client-IP", async () => {
    const fetchSpy = upstream();

    await POST(
      makeProxyRequest("http://localhost:3000/api/auth/login", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Forwarded-For": "203.0.113.9, 10.0.0.2",
          "X-Client-IP": "198.51.100.44",
        },
        body: "{}",
      }),
      ctx(["auth", "login"]),
    );

    const sent = new Headers(
      (fetchSpy.mock.calls[0] as unknown as [string, RequestInit])[1].headers,
    );
    // 2026-11-09 语义：默认不信任客户端可伪造的转发头，改用服务端签发的
    // rl Cookie 分桶键
    expect(sent.get("x-client-ip")).toMatch(/^rl-[0-9a-f-]{36}$/);
    expect(sent.get("x-client-ip")).not.toBe("203.0.113.9");
    expect(sent.get("x-client-ip")).not.toBe("198.51.100.44");
  });

  it("显式 TRUST_PROXY_HEADERS=true 时才采信 XFF 首段", async () => {
    vi.stubEnv("TRUST_PROXY_HEADERS", "true");
    try {
      const fetchSpy = upstream();
      await POST(
        makeProxyRequest("http://localhost:3000/api/auth/login", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Forwarded-For": "203.0.113.9, 10.0.0.2",
          },
          body: "{}",
        }),
        ctx(["auth", "login"]),
      );
      const sent = new Headers(
        (fetchSpy.mock.calls[0] as unknown as [string, RequestInit])[1].headers,
      );
      expect(sent.get("x-client-ip")).toBe("203.0.113.9");
    } finally {
      vi.unstubAllEnvs();
    }
  });

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
        headers: {
          "idempotency-key": "idem-lower-1",
          Authorization: "Bearer user-token",
        },
        body: "{}",
      }),
      ctx(["x"]),
    );

    const sent = new Headers((fetchSpy.mock.calls[0] as unknown as [string, RequestInit])[1].headers);
    expect(sent.get("idempotency-key")).toBe("idem-lower-1");
  });

  it("RAG 上传的 X-Batch-Id 必须原样到达上游（2026-11-10 评审回归：批次关联此前被剥头静默失效）", async () => {
    const fetchSpy = upstream();

    await POST(
      makeProxyRequest("http://localhost:3000/api/rag/upload", {
        method: "POST",
        headers: {
          "Content-Type": "multipart/form-data; boundary=x",
          "X-Batch-Id": "batch-abc",
          "Idempotency-Key": "idem-upload-1",
          Authorization: "Bearer user-token",
        },
        body: "payload",
      }),
      ctx(["rag", "upload"]),
    );

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const sent = new Headers(
      (fetchSpy.mock.calls[0] as unknown as [string, RequestInit])[1].headers,
    );
    expect(sent.get("x-batch-id")).toBe("batch-abc");
    expect(sent.get("idempotency-key")).toBe("idem-upload-1");
  });

  it("请求体流式透传且内容完整（2026-11-10 评审回归：上传不再整包读入 Node 内存）", async () => {
    const fetchSpy = upstream();

    await POST(
      makeProxyRequest("http://localhost:3000/api/rag/upload", {
        method: "POST",
        headers: { "Content-Type": "text/plain", Authorization: "Bearer t" },
        body: "hello-upload-body",
      }),
      ctx(["rag", "upload"]),
    );

    const [, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.body).toBeTruthy();
    // 流式直通：上游拿到 ReadableStream，而非一次性读入的 ArrayBuffer/字符串
    expect(init.body).not.toBeInstanceOf(ArrayBuffer);
    expect(typeof init.body).not.toBe("string");
    const text = await new Response(init.body as ReadableStream).text();
    expect(text).toBe("hello-upload-body");
  });

  it("RAG 上传超路径上限（50MiB+16KiB）在 BFF 直接 413 人话拒绝，不触达上游（2026-11-10 冒烟回归）", async () => {
    // 背景：流式透传场景下，超限 body 全量转发后上游 413 断连会触发
    // undici「terminated」，客户端收到连接重置（000）而非 413。修复：与
    // 后端 body_limit 三方同源的上限在 BFF 预检拒绝。只声明超限
    // Content-Length，不需要真实大 body。
    const fetchSpy = upstream();

    const response = await POST(
      makeProxyRequest("http://localhost:3000/api/rag/upload", {
        method: "POST",
        headers: {
          "Content-Type": "multipart/form-data; boundary=x",
          "Content-Length": String(50 * 1024 * 1024 + 16 * 1024 + 1),
          Authorization: "Bearer t",
        },
        body: "x",
      }),
      ctx(["rag", "upload"]),
    );

    expect(response.status).toBe(413);
    expect(fetchSpy).not.toHaveBeenCalled();
    const body = (await response.json()) as { error?: string; detail?: string };
    expect(body.error).toBe("PayloadTooLarge");
    // detail 人话对齐后端 body_limit 413 字段结构（前端解析链 detail 优先）
    expect(body.detail).toContain("请求体超过上限");
  });

  it("非上传路径沿用默认 64MB 上限，51MB JSON 请求不被拦（同批次回归）", async () => {
    const fetchSpy = upstream();

    await POST(
      makeProxyRequest("http://localhost:3000/api/selection-funnel/import", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": String(51 * 1024 * 1024),
          Authorization: "Bearer t",
        },
        body: "{}",
      }),
      ctx(["selection-funnel", "import"]),
    );

    expect(fetchSpy).toHaveBeenCalledTimes(1);
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
        headers: { "X-Evil": "nope", Authorization: "Bearer user-token" },
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

  it("仅匿名分享读取和 ICS 导出转发令牌并透传安全响应头", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("shared", {
        headers: {
          "Content-Type": "text/calendar; charset=utf-8",
          "Cache-Control": "no-store, private",
          "Referrer-Policy": "no-referrer",
          "X-Robots-Tag": "noindex, nofollow",
          Vary: "X-Travel-Share-Token",
        },
      }),
    );
    process.env.API_KEY = "server-only-key";
    const token = ["opaque-share-token", "not-in-url"].join("-");

    try {
      const response = await GET(
        makeProxyRequest(
          "http://localhost:3000/api/travel/v2/shares/123e4567-e89b-42d3-a456-426614174000/export.ics",
          { method: "GET", headers: { "X-Travel-Share-Token": token } },
        ),
        ctx([
          "travel",
          "v2",
          "shares",
          "123e4567-e89b-42d3-a456-426614174000",
          "export.ics",
        ]),
      );

      const [target, init] = fetchSpy.mock.calls[0] as unknown as [
        string,
        RequestInit,
      ];
      const sent = new Headers(init.headers);
      expect(target).not.toContain(token);
      expect(sent.get("x-travel-share-token")).toBe(token);
      expect(sent.get("authorization")).toBeNull();
      expect(sent.get("x-api-key")).toBe("server-only-key");
      expect(response.headers.get("cache-control")).toBe("no-store, private");
      expect(response.headers.get("referrer-policy")).toBe("no-referrer");
      expect(response.headers.get("x-robots-tag")).toBe("noindex, nofollow");
      expect(response.headers.get("vary")).toBe("X-Travel-Share-Token");
    } finally {
      delete process.env.API_KEY;
    }
  });

  it("私有 Trip 路由不转发分享令牌或公开分享响应头", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("private", {
        headers: {
          "Content-Type": "application/json",
          "Cache-Control": "no-store",
          "Referrer-Policy": "no-referrer",
          "X-Robots-Tag": "noindex",
          Vary: "X-Travel-Share-Token",
        },
      }),
    );
    const response = await GET(
      makeProxyRequest(
        "http://localhost:3000/api/travel/v2/trips/123e4567-e89b-42d3-a456-426614174000",
        {
          method: "GET",
          headers: {
            "X-Travel-Share-Token": ["must-not", "forward"].join("-"),
            Authorization: "Bearer user-token",
          },
        },
      ),
      ctx(["travel", "v2", "trips", "123e4567-e89b-42d3-a456-426614174000"]),
    );

    const [, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    const sent = new Headers(init.headers);
    expect(sent.get("x-travel-share-token")).toBeNull();
    expect(response.headers.get("cache-control")).toBeNull();
    expect(response.headers.get("referrer-policy")).toBeNull();
    expect(response.headers.get("x-robots-tag")).toBeNull();
    expect(response.headers.get("vary")).toBeNull();
  });

  it("创建分享响应保留 no-store 头但不转发匿名读取令牌头", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ share_url: "/travel/share/id#token=opaque" }), {
        headers: {
          "Content-Type": "application/json",
          "Cache-Control": "no-store",
          "Referrer-Policy": "no-referrer",
          "X-Robots-Tag": "noindex, nofollow",
        },
      }),
    );
    const response = await POST(
      makeProxyRequest(
        "http://localhost:3000/api/travel/v2/trips/123e4567-e89b-42d3-a456-426614174000/shares",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Travel-Share-Token": ["must-not-forward", "on-create"].join("-"),
            Authorization: "Bearer user-token",
          },
          body: JSON.stringify({ revision: 1 }),
        },
      ),
      ctx([
        "travel",
        "v2",
        "trips",
        "123e4567-e89b-42d3-a456-426614174000",
        "shares",
      ]),
    );

    const [, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(new Headers(init.headers).get("x-travel-share-token")).toBeNull();
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("referrer-policy")).toBe("no-referrer");
    expect(response.headers.get("x-robots-tag")).toBe("noindex, nofollow");
  });

  it("网关连接错误不回显异常内容", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(
      new Error("token=secret database=postgres://private"),
    );
    const response = await GET(
      makeProxyRequest("http://localhost:3000/api/travel/v2/shares/123e4567-e89b-42d3-a456-426614174000", {
        method: "GET",
        headers: { "X-Travel-Share-Token": "opaque" },
      }),
      ctx(["travel", "v2", "shares", "123e4567-e89b-42d3-a456-426614174000"]),
    );

    expect(response.status).toBe(502);
    const body = await response.text();
    expect(body).not.toContain("postgres://private");
    expect(body).not.toContain("token=secret");
  });

  it("私有 API 缺少 Bearer 时在 BFF 拒绝，不注入服务密钥", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    process.env.API_KEY = "server-only-key";
    try {
      const response = await POST(
        makeProxyRequest("http://localhost:3000/api/private/write", {
          method: "POST",
          headers: { "X-Evil": "nope" },
          body: "{}",
        }),
        ctx(["private", "write"]),
      );
      expect(response.status).toBe(401);
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      delete process.env.API_KEY;
    }
  });

  it("登录与验证码公共入口仍可匿名转发", async () => {
    const fetchSpy = upstream();
    await POST(
      makeProxyRequest("http://localhost:3000/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      }),
      ctx(["auth", "login"]),
    );
    await GET(
      makeProxyRequest("http://localhost:3000/api/sys/users/captcha", {
        method: "GET",
      }),
      ctx(["sys", "users", "captcha"]),
    );
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("登录路径的额外后缀不会获得匿名白名单", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const response = await POST(
      makeProxyRequest("http://localhost:3000/api/auth/login/extra", {
        method: "POST",
        body: "{}",
      }),
      ctx(["auth", "login", "extra"]),
    );

    expect(response.status).toBe(401);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
