import { describe, expect, it } from "vitest";
import { ApiError } from "./client";
import { describeApiError } from "./errors";

describe("管理端统一错误封套", () => {
  it("保留预算动作所需的协议字段", () => {
    const error = new ApiError("预算超限", 429, {
      code: "BUDGET_EXCEEDED",
      retryable: false,
      handoff_available: false,
      trace_id: "trace-admin",
      source: "quota",
      details: { budget_kind: "tenant", limit_kind: "monthly" },
    }, "BUDGET_EXCEEDED");

    expect(describeApiError(error)).toMatchObject({
      retryable: false,
      handoffAvailable: false,
      traceId: "trace-admin",
      details: { budget_kind: "tenant", limit_kind: "monthly" },
    });
  });
});
