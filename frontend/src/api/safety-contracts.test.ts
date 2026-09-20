import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, mutationRequest } from "./client";
import { getIdempotencyOperationStatus } from "./idempotency";
import { describeApiError } from "./errors";

describe("统一错误封套", () => {
  it("保留 retryable、handoff、trace、source 和 details", () => {
    const error = new ApiError("预算已用尽", 429, {
      code: "BUDGET_EXCEEDED",
      retryable: false,
      handoff_available: true,
      trace_id: "trace-1",
      source: "quota",
      details: {
        budget_kind: "user",
        limit_kind: "daily",
        reset_at: "2026-09-18T16:00:00Z",
      },
    }, "BUDGET_EXCEEDED");

    expect(describeApiError(error)).toMatchObject({
      code: "BUDGET_EXCEEDED",
      retryable: false,
      handoffAvailable: true,
      traceId: "trace-1",
      source: "quota",
      details: {
        budget_kind: "user",
        limit_kind: "daily",
      },
    });
  });
});

describe("mutationRequest 幂等请求", () => {
  afterEach(() => vi.restoreAllMocks());

  it("同一逻辑操作并发 20 次只发送一个请求并复用键", async () => {
    const requests: Array<{ key: string | null }> = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, init) => {
      const headers = (init?.headers ?? {}) as Record<string, string>;
      requests.push({ key: headers["Idempotency-Key"] ?? null });
      await new Promise((resolve) => setTimeout(resolve, 5));
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    });

    const results = await Promise.all(
      Array.from({ length: 20 }, () =>
        mutationRequest<{ ok: boolean }>("/api/write", {
          method: "POST",
          body: { value: "same" },
          operation: "write:same",
        }),
      ),
    );

    expect(requests).toHaveLength(1);
    expect(new Set(requests.map((item) => item.key)).size).toBe(1);
    expect(results.every((item) => item.ok)).toBe(true);
  });

  it("参数变化后生成新键", async () => {
    const keys: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, init) => {
      const headers = (init?.headers ?? {}) as Record<string, string>;
      keys.push(headers["Idempotency-Key"] ?? "");
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    });

    await mutationRequest("/api/write", {
      method: "POST",
      body: { value: "a" },
      operation: "write",
    });
    await mutationRequest("/api/write", {
      method: "POST",
      body: { value: "b" },
      operation: "write",
    });

    expect(keys).toHaveLength(2);
    expect(keys[0]).not.toBe(keys[1]);
  });

  it("幂等冲突错误保留原操作键，供状态查询使用", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({
        detail: { code: "IDEMPOTENCY_CONFLICT", message: "请求已处理" },
      }), { status: 409 }),
    );

    await expect(
      mutationRequest("/api/write", {
        method: "POST",
        body: { value: "same" },
        operation: "write",
        idempotencyKey: "req-status-1",
      }),
    ).rejects.toMatchObject({
      code: "IDEMPOTENCY_CONFLICT",
      idempotencyKey: "req-status-1",
    });
  });

  it("幂等状态查询只调用当前用户状态接口", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({
        client_key: "req-status-1",
        operation: "write",
        status: "succeeded",
        attempt: 1,
        has_result: true,
      }), { status: 200 }),
    );

    const result = await getIdempotencyOperationStatus("req-status-1");

    expect(result.status).toBe("succeeded");
    expect(String(fetchSpy.mock.calls[0][0])).toContain(
      "/api/idempotency/operations/req-status-1",
    );
  });
});
