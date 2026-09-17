/**
 * ErrorCard 反馈语义测试（UX 设计 P0-②）
 *
 * 锁四件事：
 *   1. RAG 拒答三语义码 → 各自的行动指引（预登记契约：后端 C4 接通前，
 *      这些码不会被后端发送；映射写在这里保证接通时零改动点亮）；
 *   2. HTTP 派生类（401/403/429/超时/网络）→ 按 kind 给行动指引，
 *      验证与 errors.ts 既有机制的衔接而非重复实现；
 *   3. RAG 语义码优先级高于 kind 映射（码命中后不再被 kind 覆盖）；
 *   4. 垃圾输入永不抛错（describeApiError 降级链的安全继承）。
 */

import { describe, expect, it } from "vitest";

import {
  KIND_ACTIONS,
  RAG_REJECTION_ACTIONS,
  resolveErrorFeedback,
} from "./ErrorCard";
import { FALLBACK_BY_STATUS, TIMEOUT_REASON } from "@/api/errors";

/** 类 ApiError 构造（同 errors.test.ts 惯例，不 import client.ts） */
function fakeApiError(code: string | undefined, status: number | undefined) {
  const e = new Error("boom") as Error & { code?: string; status?: number };
  if (code !== undefined) e.code = code;
  if (status !== undefined) e.status = status;
  return e;
}

describe("RAG 拒答三语义码 → 行动指引（预登记契约）", () => {
  it("RAG_NO_EVIDENCE → 换问法指引", () => {
    const fb = resolveErrorFeedback(fakeApiError("RAG_NO_EVIDENCE", 200));
    expect(fb.actionText).toBe(RAG_REJECTION_ACTIONS.RAG_NO_EVIDENCE);
    expect(fb.actionText).toContain("换个问法");
  });

  it("RAG_PERMISSION_DENIED → 申请权限指引", () => {
    const fb = resolveErrorFeedback(fakeApiError("RAG_PERMISSION_DENIED", 200));
    expect(fb.actionText).toBe(RAG_REJECTION_ACTIONS.RAG_PERMISSION_DENIED);
    expect(fb.actionText).toContain("权限");
  });

  it("RAG_VERSION_CONFLICT → 版本确认指引", () => {
    const fb = resolveErrorFeedback(fakeApiError("RAG_VERSION_CONFLICT", 200));
    expect(fb.actionText).toBe(RAG_REJECTION_ACTIONS.RAG_VERSION_CONFLICT);
    expect(fb.actionText).toContain("版本");
  });
});

describe("HTTP 派生类 → kind 行动指引（衔接 errors.ts 既有机制）", () => {
  it("401 → auth：重新登录", () => {
    const fb = resolveErrorFeedback(fakeApiError(undefined, 401));
    expect(fb.kind).toBe("auth");
    expect(fb.actionText).toBe(KIND_ACTIONS.auth);
    expect(fb.message).toBe(FALLBACK_BY_STATUS[401].message);
  });

  it("403 → permission：联系管理员", () => {
    const fb = resolveErrorFeedback(fakeApiError(undefined, 403));
    expect(fb.actionText).toBe(KIND_ACTIONS.permission);
  });

  it("429 → rate_limit：稍候再试", () => {
    const fb = resolveErrorFeedback(fakeApiError(undefined, 429));
    expect(fb.actionText).toBe(KIND_ACTIONS.rate_limit);
    expect(fb.retriable).toBe(true);
  });

  it("超时 → timeout 指引且可重试", () => {
    const fb = resolveErrorFeedback(new Error(TIMEOUT_REASON));
    expect(fb.kind).toBe("timeout");
    expect(fb.actionText).toBe(KIND_ACTIONS.timeout);
    expect(fb.retriable).toBe(true);
  });

  it("网络不可达 → network 指引", () => {
    const fb = resolveErrorFeedback(new TypeError("Failed to fetch"));
    expect(fb.actionText).toBe(KIND_ACTIONS.network);
  });
});

describe("映射优先级与健壮性", () => {
  it("RAG 语义码优先于 kind 映射（码命中后不被 kind 覆盖）", () => {
    // 403 的 kind 是 permission，但 RAG 语义码命中时应给 RAG 专属指引
    const fb = resolveErrorFeedback(fakeApiError("RAG_PERMISSION_DENIED", 403));
    expect(fb.actionText).toBe(RAG_REJECTION_ACTIONS.RAG_PERMISSION_DENIED);
    expect(fb.actionText).not.toBe(KIND_ACTIONS.permission);
  });

  it("未登记的码走 kind 兜底，不落空", () => {
    const fb = resolveErrorFeedback(fakeApiError("UNKNOWN_MIDDLE_TIER", 500));
    expect(fb.actionText).toBe(KIND_ACTIONS.server);
  });

  it("垃圾输入永不抛错且始终有行动指引", () => {
    const junk: unknown[] = [undefined, null, 0, "", "x", [], { status: "5" }, Symbol("s")];
    for (const v of junk) {
      const fb = resolveErrorFeedback(v);
      expect(typeof fb.actionText).toBe("string");
      expect(fb.actionText.length).toBeGreaterThan(0);
    }
  });
});
