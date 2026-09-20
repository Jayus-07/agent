/**
 * P0-1a 网络层契约测试
 *
 * 目的：把「拆分前后行为等价」这条锁住 —— 尤其是 401→refresh→重试一次 的语义、
 * 请求头合并顺序、以及多后端基址的回落规则。这三条一旦被后续重构改坏，
 * 表现为「偶发跳登录页」或「鉴权头丢失」这类难查的线上问题。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// vi.hoisted：mock 工厂在 import 提升阶段就会执行，普通 const 会踩 TDZ
const authMock = vi.hoisted(() => ({
  bearerHeaders: vi.fn<() => Record<string, string>>(() => ({})),
  tryRefreshOnce: vi.fn<() => Promise<boolean>>(),
  handleAuthFailure: vi.fn<() => void>(),
}));

vi.mock("@/lib/auth", () => authMock);

import {
  ApiError,
  backendBaseUrl,
  fetchRaw,
  mutationFetchRaw,
  request,
  requestSilent,
} from "./client";
import { CLIENT_ERROR_CODES, describeApiError } from "./errors";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const originalApiUrl = process.env.NEXT_PUBLIC_API_URL;
const originalBizUrl = process.env.NEXT_PUBLIC_BUSINESS_API_URL;
const originalApiKey = process.env.NEXT_PUBLIC_API_KEY;

beforeEach(() => {
  vi.restoreAllMocks();
  authMock.bearerHeaders.mockReturnValue({});
  authMock.tryRefreshOnce.mockReset();
  authMock.handleAuthFailure.mockReset();
  delete process.env.NEXT_PUBLIC_API_URL;
  delete process.env.NEXT_PUBLIC_BUSINESS_API_URL;
  delete process.env.NEXT_PUBLIC_API_KEY;
});

afterEach(() => {
  if (originalApiUrl === undefined) delete process.env.NEXT_PUBLIC_API_URL;
  else process.env.NEXT_PUBLIC_API_URL = originalApiUrl;
  if (originalBizUrl === undefined) delete process.env.NEXT_PUBLIC_BUSINESS_API_URL;
  else process.env.NEXT_PUBLIC_BUSINESS_API_URL = originalBizUrl;
  if (originalApiKey === undefined) delete process.env.NEXT_PUBLIC_API_KEY;
  else process.env.NEXT_PUBLIC_API_KEY = originalApiKey;
});

describe("backendBaseUrl 多后端映射", () => {
  it("未配置任何基址时返回空串（同源相对路径，等价拆分前默认值）", () => {
    expect(backendBaseUrl()).toBe("");
    expect(backendBaseUrl("core")).toBe("");
    expect(backendBaseUrl("business")).toBe("");
  });

  it("business 未配置时回落 core —— 保证拆分前后行为一致", () => {
    process.env.NEXT_PUBLIC_API_URL = "http://core.test";
    expect(backendBaseUrl("business")).toBe("http://core.test");
  });

  it("business 配置了独立基址时各自生效（业务迁独立服务场景）", () => {
    process.env.NEXT_PUBLIC_API_URL = "http://core.test";
    process.env.NEXT_PUBLIC_BUSINESS_API_URL = "http://biz.test";
    expect(backendBaseUrl("core")).toBe("http://core.test");
    expect(backendBaseUrl("business")).toBe("http://biz.test");
  });

  it("剥离尾部斜杠，避免拼出双斜杠路径", () => {
    process.env.NEXT_PUBLIC_API_URL = "http://core.test/";
    expect(backendBaseUrl("core")).toBe("http://core.test");
  });
});

describe("request 请求构造", () => {
  it("合并 Bearer 与调用方 headers，且调用方优先级最高（凭据收口后浏览器不带 Key）", async () => {
    authMock.bearerHeaders.mockReturnValue({ Authorization: "Bearer t" });
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ ok: true }));

    await request("/x", { headers: { "X-Custom": "1" } });

    const init = fetchSpy.mock.calls[0][1] as RequestInit;
    expect(init.headers).toMatchObject({
      "Content-Type": "application/json",
      Authorization: "Bearer t",
      "X-Custom": "1",
    });
    // 凭据收口（方案 B）：浏览器侧不再注入 X-API-Key（由 BFF 代理路由注入）
    expect((init.headers as Record<string, string>)["X-API-Key"]).toBeUndefined();
  });

  it("调用方可用 headers 覆写默认值（含 Content-Type）", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ ok: true }));

    await request("/x", { headers: { "Content-Type": "text/plain" } });

    const init = fetchSpy.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("text/plain");
  });

  it("绝对 URL 原样透传，不拼基址", async () => {
    process.env.NEXT_PUBLIC_API_URL = "http://core.test";
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({}));

    await request("http://other.test/abs");

    expect(fetchSpy.mock.calls[0][0]).toBe("http://other.test/abs");
  });

  it("按 backend 选择基址", async () => {
    process.env.NEXT_PUBLIC_API_URL = "http://core.test";
    process.env.NEXT_PUBLIC_BUSINESS_API_URL = "http://biz.test";
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({}));

    await request("/cs/conversations", { backend: "business" });

    expect(fetchSpy.mock.calls[0][0]).toBe("http://biz.test/cs/conversations");
  });

  it("相对路径自动补前导斜杠", async () => {
    process.env.NEXT_PUBLIC_API_URL = "http://core.test";
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({}));

    await request("prompts");

    expect(fetchSpy.mock.calls[0][0]).toBe("http://core.test/prompts");
  });
});

describe("request 错误模型", () => {
  it("非 2xx 抛 ApiError，携带 status 与 FastAPI detail", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ detail: "boom" }, 500),
    );

    const err = await request("/x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(500);
    expect((err as ApiError).message).toBe("boom");
    expect((err as ApiError).detail).toBe("boom");
  });

  it("detail 为对象时取 error/message 作为文案", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ detail: { error: "E_BAD" } }, 400),
    );

    const err = (await request("/x").catch((e: unknown) => e)) as ApiError;
    expect(err.message).toBe("E_BAD");
  });

  it("响应体非 JSON 时不炸（降级为 statusText）", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("<html>500</html>", { status: 500, statusText: "Server Error" }),
    );

    const err = (await request("/x").catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(500);
    expect(err.message).toBe("Server Error");
  });

  it("requestSilent 吞掉错误，不向外抛", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ detail: "no" }, 500));
    await expect(requestSilent("/x")).resolves.toBeUndefined();
  });
});

describe("401 语义（拆分前后必须一致）", () => {
  it("刷新成功后重试原请求且只重试一次", async () => {
    authMock.tryRefreshOnce.mockResolvedValue(true);
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ detail: "unauth" }, 401))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));

    await expect(request("/protected")).resolves.toEqual({ ok: true });
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(authMock.handleAuthFailure).not.toHaveBeenCalled();
  });

  it("重试后仍 401 不再递归刷新（防无限循环）", async () => {
    authMock.tryRefreshOnce.mockResolvedValue(true);
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ detail: "unauth" }, 401));

    const err = (await request("/protected").catch((e: unknown) => e)) as ApiError;
    expect(err.status).toBe(401);
    expect(authMock.tryRefreshOnce).toHaveBeenCalledTimes(1);
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("刷新失败 → 触发 handleAuthFailure（清态跳登录页）", async () => {
    authMock.tryRefreshOnce.mockResolvedValue(false);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ detail: "unauth" }, 401),
    );

    await expect(request("/protected")).rejects.toBeInstanceOf(ApiError);
    expect(authMock.handleAuthFailure).toHaveBeenCalledTimes(1);
  });

  it("auth 端点自身的 401 不触发刷新（防递归）", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ detail: "bad credentials" }, 401),
    );

    await expect(request("/api/auth/login")).rejects.toBeInstanceOf(ApiError);
    expect(authMock.tryRefreshOnce).not.toHaveBeenCalled();
    expect(authMock.handleAuthFailure).not.toHaveBeenCalled();
  });

  it("跨网关绝对形式的 auth 路径同样不触发刷新", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ detail: "bad" }, 401),
    );

    await expect(
      request("http://gw.test/api/auth/refresh"),
    ).rejects.toBeInstanceOf(ApiError);
    expect(authMock.tryRefreshOnce).not.toHaveBeenCalled();
  });

  it("fetchRaw 同样具备刷新重试语义", async () => {
    authMock.tryRefreshOnce.mockResolvedValue(true);
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ detail: "unauth" }, 401))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));

    const res = await fetchRaw("/stream");
    expect(res.status).toBe(200);
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });
});

describe("mutationFetchRaw 幂等原始写请求", () => {
  it("注入稳定幂等键，并在 401 刷新后复用同一个键", async () => {
    authMock.tryRefreshOnce.mockResolvedValue(true);
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ detail: "unauth" }, 401))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));

    const response = await mutationFetchRaw("/upload", {
      operation: "rag.upload",
      idempotencyKey: "upload-key-1",
      dedupeKey: "file-1",
      method: "POST",
      body: new FormData(),
    });

    expect(response.status).toBe(200);
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    for (const call of fetchSpy.mock.calls) {
      const headers = new Headers((call[1] as RequestInit).headers);
      expect(headers.get("Idempotency-Key")).toBe("upload-key-1");
    }
  });

  it("同一原始写操作在途时只发一个请求，并为调用方提供可读取的响应副本", async () => {
    let release!: (response: Response) => void;
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(
      () => new Promise<Response>((resolve) => { release = resolve; }),
    );

    const first = mutationFetchRaw("/write", {
      operation: "write",
      dedupeKey: "same-payload",
      method: "POST",
      body: new FormData(),
    });
    const second = mutationFetchRaw("/write", {
      operation: "write",
      dedupeKey: "same-payload",
      method: "POST",
      body: new FormData(),
    });
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    release(jsonResponse({ ok: true }));
    const [firstResponse, secondResponse] = await Promise.all([first, second]);
    await expect(firstResponse.json()).resolves.toEqual({ ok: true });
    await expect(secondResponse.json()).resolves.toEqual({ ok: true });
  });
});

describe("超时与取消", () => {
  it("超时后 abort，并携 Request timeout 原因", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((_url, init) => {
      const signal = (init as RequestInit)?.signal;
      return new Promise<Response>((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(signal.reason));
      });
    });

    await expect(request("/slow", { timeout: 10 })).rejects.toThrow(
      "Request timeout",
    );
  });

  it("外部 signal 已 aborted 时立即传播原因", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((_url, init) => {
      const signal = (init as RequestInit)?.signal;
      return new Promise<Response>((_resolve, reject) => {
        if (signal?.aborted) return reject(signal.reason);
        signal?.addEventListener("abort", () => reject(signal.reason));
      });
    });

    const controller = new AbortController();
    controller.abort(new Error("user cancelled"));

    await expect(
      request("/x", { signal: controller.signal, timeout: 5000 }),
    ).rejects.toThrow("user cancelled");
  });
});

describe("P0-1b 错误码提取与翻译", () => {
  it("顶层 code 被提取到 ApiError.code", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ code: "RAG_TEST_001", detail: "索引失败" }, 500),
    );

    const err = (await request("/x").catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("RAG_TEST_001");
    expect(err.message).toBe("索引失败");
  });

  it("兼容 detail.code 写法", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ detail: { code: "AUTH_TOKEN_EXPIRED", message: "过期" } }, 401),
    );

    const err = (await request("/x").catch((e: unknown) => e)) as ApiError;
    expect(err.code).toBe("AUTH_TOKEN_EXPIRED");
  });

  it("兼容 detail.error_code 写法", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ detail: { error_code: "E_LEGACY", error: "旧格式" } }, 400),
    );

    const err = (await request("/x").catch((e: unknown) => e)) as ApiError;
    expect(err.code).toBe("E_LEGACY");
  });

  it("无 code 时为 undefined（向后兼容：老接口不受影响）", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ detail: "boom" }, 500),
    );

    const err = (await request("/x").catch((e: unknown) => e)) as ApiError;
    expect(err.code).toBeUndefined();
    expect(err.status).toBe(500);
  });

  it("code 为非字符串时忽略，不污染错误模型", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ code: 500, detail: "x" }, 500),
    );

    const err = (await request("/x").catch((e: unknown) => e)) as ApiError;
    expect(err.code).toBeUndefined();
  });

  it("端到端：ApiError 交给 describeApiError 能拿到兜底文案", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ detail: "无权访问" }, 403),
    );

    const err = (await request("/x").catch((e: unknown) => e)) as ApiError;
    const desc = describeApiError(err);
    expect(desc.kind).toBe("permission");
    expect(desc.retriable).toBe(false);
    expect(desc.status).toBe(403);
    // 界面上展示的是降级后的中文文案，而不是后端英文 detail
    expect(desc.message).not.toBe("无权访问");
  });

  it("超时异常经 describeApiError 归为 timeout（与 client 的终止原因对齐）", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((_url, init) => {
      const signal = (init as RequestInit)?.signal;
      return new Promise<Response>((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(signal.reason));
      });
    });

    const err = await request("/slow", { timeout: 10 }).catch((e: unknown) => e);
    expect(describeApiError(err).kind).toBe("timeout");
    expect(describeApiError(err).code).toBe(CLIENT_ERROR_CODES.TIMEOUT);
  });
});
