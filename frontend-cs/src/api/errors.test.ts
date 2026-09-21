/**
 * 错误码机制契约测试（P0-1b）
 *
 * 重点锁三件事：
 *   1. **降级链不可抛错** —— 垃圾输入、无状态、未知码都必须有可展示文案；
 *   2. **合成码能真的走通** —— 用 `RAG_TEST_001` 验证「登记 → 命中 → 文案」全链路，
 *      不依赖后端定义真实码（后端 `RAG_XXXX` 目前不存在）；
 *   3. **客户端异常分类正确** —— 超时 / 取消 / 网络不可达三者不能互相误判，
 *      它们分别要求「重试」「静默」「重试」三种完全不同的交互。
 */

import { afterEach, describe, expect, it } from "vitest";

import {
  CLIENT_ERROR_CODES,
  PROTOCOL_ERROR_CODES,
  DOMAIN_ERRORS,
  FALLBACK_BY_STATUS,
  TIMEOUT_REASON,
  UNKNOWN_ERROR,
  describeApiError,
  isCanceledError,
  isClientErrorCode,
  isDomainErrorCode,
  isTimeoutError,
  registerDomainErrors,
  toUserMessage,
} from "./errors";

/** 合成码：后端真实码尚未定义，用假码验证机制本身 */
const SYNTHETIC_CODE = "RAG_TEST_001";

/** 登记表是模块级单例，测试登记过的键必须清理，避免污染其他用例 */
const registeredKeys: string[] = [];
afterEach(() => {
  const table = DOMAIN_ERRORS as Record<string, unknown>;
  for (const k of registeredKeys) delete table[k];
  registeredKeys.length = 0;
});

/** 构造一个带 code / status 的「类 ApiError」—— 不 import client.ts，保持本文件零依赖 */
function fakeApiError(code: string | undefined, status: number | undefined) {
  const e = new Error("boom") as Error & { code?: string; status?: number };
  if (code !== undefined) e.code = code;
  if (status !== undefined) e.status = status;
  return e;
}

describe("降级链：未登记的码不落到「未知」", () => {
  it("有 status、无码 → 按状态兜底", () => {
    const r = describeApiError(fakeApiError(undefined, 403));
    expect(r).toMatchObject(FALLBACK_BY_STATUS[403]);
    expect(r.code).toBeUndefined();
    expect(r.status).toBe(403);
  });

  it("有未登记的具体码也不影响状态兜底", () => {
    const r = describeApiError(fakeApiError("MIDDLE_TIER_UNKNOWN", 404));
    expect(r.kind).toBe("not_found");
    expect(r.code).toBe("MIDDLE_TIER_UNKNOWN");
  });

  it("未登记的 4xx 按 400 大类兜底（不落 unknown）", () => {
    const r = describeApiError(fakeApiError(undefined, 418));
    expect(r.kind).toBe("validation");
    expect(r.message).toBe(FALLBACK_BY_STATUS[400].message);
  });

  it("未登记的 5xx 按 500 大类兜底（不落 unknown）", () => {
    const r = describeApiError(fakeApiError(undefined, 599));
    expect(r.kind).toBe("server");
    expect(r.retriable).toBe(true);
  });

  it("既无码也无状态 → 全兜底", () => {
    expect(describeApiError(new Error("???")).message).toBe(UNKNOWN_ERROR.message);
  });
});

describe("合成码：登记 → 命中 → 文案 全链路", () => {
  it("登记前：合成码不生效，走状态兜底", () => {
    const r = describeApiError(fakeApiError(SYNTHETIC_CODE, 500));
    expect(r.code).toBe(SYNTHETIC_CODE);
    expect(r.kind).toBe("server"); // 状态兜底，而非登记表
  });

  it("登记后：命中登记文案并保留码/状态/cause", () => {
    const err = fakeApiError(SYNTHETIC_CODE, 500);
    registeredKeys.push(SYNTHETIC_CODE);
    registerDomainErrors({
      [SYNTHETIC_CODE]: {
        message: "知识库索引超时，请稍后重试",
        kind: "timeout",
        retriable: true,
      },
    });

    const r = describeApiError(err);
    expect(r.message).toBe("知识库索引超时，请稍后重试");
    expect(r.kind).toBe("timeout");
    expect(r.retriable).toBe(true);
    expect(r.code).toBe(SYNTHETIC_CODE);
    expect(r.status).toBe(500);
    expect(r.cause).toBe(err);
  });

  it("域码优先于状态码兜底（同一个 500 不再显示通用文案）", () => {
    registeredKeys.push(SYNTHETIC_CODE);
    registerDomainErrors({
      [SYNTHETIC_CODE]: { message: "专用文案", kind: "validation", retriable: false },
    });
    const r = describeApiError(fakeApiError(SYNTHETIC_CODE, 500));
    expect(r.message).toBe("专用文案");
    expect(r.retriable).toBe(false);
  });

  it("registerDomainErrors 返回实际登记条数", () => {
    registeredKeys.push(SYNTHETIC_CODE, "RAG_TEST_002");
    const n = registerDomainErrors({
      [SYNTHETIC_CODE]: { message: "a", kind: "server", retriable: true },
      RAG_TEST_002: { message: "b", kind: "server", retriable: true },
    });
    expect(n).toBe(2);
  });
});

describe("客户端异常分类：超时 / 取消 / 网络 不可互串", () => {
  it("超时（TIMEOUT_REASON）→ timeout 且可重试", () => {
    const err = new Error(TIMEOUT_REASON);
    expect(isTimeoutError(err)).toBe(true);
    const r = describeApiError(err);
    expect(r.kind).toBe("timeout");
    expect(r.code).toBe(CLIENT_ERROR_CODES.TIMEOUT);
    expect(r.retriable).toBe(true);
  });

  it("主动取消（AbortError）→ canceled 且不重试", () => {
    const err = new Error("user navigated away");
    err.name = "AbortError";
    expect(isCanceledError(err)).toBe(true);
    const r = describeApiError(err);
    expect(r.kind).toBe("canceled");
    expect(r.code).toBe(CLIENT_ERROR_CODES.CANCELED);
    expect(r.retriable).toBe(false);
  });

  it("超时不被误判为「主动取消」（名字相同、语义相反）", () => {
    const err = new Error(TIMEOUT_REASON);
    err.name = "AbortError"; // 最坏情况：两者同时成立
    expect(isCanceledError(err)).toBe(false);
    expect(describeApiError(err).kind).toBe("timeout");
  });

  it("TypeError（fetch 连不上）→ network 且可重试", () => {
    const r = describeApiError(new TypeError("Failed to fetch"));
    expect(r.kind).toBe("network");
    expect(r.code).toBe(CLIENT_ERROR_CODES.NETWORK);
    expect(r.retriable).toBe(true);
  });
});

describe("后端统一错误协议：九个通用码各自稳定分派", () => {
  it("未知协议码按 INTERNAL_ERROR 语义兜底，已知九码不依赖 HTTP 状态", () => {
    const expectedKinds: Record<string, string> = {
      INVALID_PARAM: "validation",
      PERMISSION_DENIED: "permission",
      NOT_FOUND: "not_found",
      TIMEOUT: "timeout",
      UPSTREAM_UNAVAILABLE: "server",
      RATE_LIMITED: "rate_limit",
      IDEMPOTENCY_CONFLICT: "conflict",
      BUDGET_EXCEEDED: "rate_limit",
      INTERNAL_ERROR: "server",
    };

    for (const code of Object.values(PROTOCOL_ERROR_CODES)) {
      const result = describeApiError(fakeApiError(code, 400));
      expect(result.code).toBe(code);
      expect(result.kind).toBe(expectedKinds[code]);
    }
    expect(describeApiError(fakeApiError("NEW_UNKNOWN_CODE", 500)).kind).toBe("server");
  });
});

describe("健壮性：永不抛错", () => {
  const junk: unknown[] = [
    undefined,
    null,
    0,
    "",
    "some string",
    [],
    { code: 123 },
    { code: "" },
    { status: "500" },
    { code: null, status: null },
    Symbol("x"),
    () => {},
  ];

  it.each(junk.map((v, i) => [i, v] as const))(
    "垃圾输入 #%i 也能给出可展示文案",
    (_i, value) => {
      const r = describeApiError(value);
      expect(typeof r.message).toBe("string");
      expect(r.message.length).toBeGreaterThan(0);
      expect(typeof r.retriable).toBe("boolean");
    },
  );

  it("toUserMessage 等价于 describeApiError().message", () => {
    expect(toUserMessage(fakeApiError(undefined, 503))).toBe(
      describeApiError(fakeApiError(undefined, 503)).message,
    );
  });
});

describe("类型守卫", () => {
  it("isDomainErrorCode 只认已登记的键", () => {
    expect(isDomainErrorCode(SYNTHETIC_CODE)).toBe(false);
    registeredKeys.push(SYNTHETIC_CODE);
    registerDomainErrors({
      [SYNTHETIC_CODE]: { message: "x", kind: "server", retriable: true },
    });
    expect(isDomainErrorCode(SYNTHETIC_CODE)).toBe(true);
    expect(isDomainErrorCode("NOT_A_CODE")).toBe(false);
    expect(isDomainErrorCode(123)).toBe(false);
    expect(isDomainErrorCode(undefined)).toBe(false);
  });

  it("isClientErrorCode 认三个客户端码", () => {
    expect(isClientErrorCode(CLIENT_ERROR_CODES.TIMEOUT)).toBe(true);
    expect(isClientErrorCode(CLIENT_ERROR_CODES.NETWORK)).toBe(true);
    expect(isClientErrorCode(CLIENT_ERROR_CODES.CANCELED)).toBe(true);
    expect(isClientErrorCode("SOMETHING_ELSE")).toBe(false);
  });

  it("域码守卫不受原型链污染影响（hasOwnProperty 判定）", () => {
    // 若实现写成 `code in DOMAIN_ERRORS`，下面这些会假阳性
    expect(isDomainErrorCode("toString")).toBe(false);
    expect(isDomainErrorCode("constructor")).toBe(false);
    expect(isClientErrorCode("hasOwnProperty")).toBe(false);
  });
});

describe("登记表本身的设计约束", () => {
  it("真实域码登记表刻意为空（后端未定义真实码，不自造）", () => {
    expect(Object.keys(DOMAIN_ERRORS)).toEqual([]);
  });

  it("状态兜底覆盖前端会真实遇到的鉴权类状态", () => {
    for (const s of [400, 401, 403, 404, 409, 413, 422, 429, 500, 502, 503, 504]) {
      expect(FALLBACK_BY_STATUS[s]).toBeDefined();
      expect(FALLBACK_BY_STATUS[s].message.length).toBeGreaterThan(0);
    }
  });

  it("401/403 标记为不可重试（重试只会再失败一次）", () => {
    expect(FALLBACK_BY_STATUS[401].retriable).toBe(false);
    expect(FALLBACK_BY_STATUS[401].kind).toBe("auth");
    expect(FALLBACK_BY_STATUS[403].retriable).toBe(false);
    expect(FALLBACK_BY_STATUS[403].kind).toBe("permission");
  });
});
