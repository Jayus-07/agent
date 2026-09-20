/**
 * src/api/client.ts — 统一网络层（P0-1a）
 *
 * 合并自 `lib/fetcher.ts`（JSON 层：request / requestSilent / ApiError）与
 * `lib/authFetch.ts`（原 Response 层：authFetch，**已于 2026-09-16 全量迁至本文件并删除**）。
 * 作为**唯一**的网络出口：新增请求一律走本文件，不要再引入第三个 fetch 包装。
 *
 * 迁移状态（2026-09-16）：
 *   - `lib/fetcher.ts` 仅 re-export（保留一个发布周期）
 *   - `lib/authFetch.ts` 已删除；原 11 个引用方（services/* 与若干 page/hook）全部改用 `fetchRaw`
 *   - 迁移前后行为等价的前提：`NEXT_PUBLIC_API_URL` 未设置时 `joinUrl` 退化为原样路径
 *     （与 authFetch 的裸 `fetch(input)` 一致）；若将来配置了该变量，**所有**调用方
 *     （含原先直连相对路径的那些）会一并切到绝对基址，属预期行为。
 *
 * 设计要点：
 * 1. **错误模型统一**：所有非 2xx 抛 `ApiError`（含 status / detail），调用方只认一种错误类型。
 * 2. **401 语义统一**：非 `/api/auth/` 路径遇 401 → 静默 refresh 一次 → 重试原请求一次；
 *    refresh 失败 → 清态跳登录页。Response 层与 JSON 层共用同一套判定。
 * 3. **基址可配置映射**：`BackendId` 支持多后端（为「业务数据迁独立服务 / 前后端彻底分离」
 *    预留）。当前 `business` 未配置时回落到 `core`，**行为与拆分前完全等价**。
 * 4. **不持有 auth 实现**：bearer/refresh/logout 全部委托 `@/lib/auth`。auth 侧重构
 *    （py 自建用户体系会话负责）只需改 `lib/auth.ts` 内部，本文件零改动。
 *
 * ⚠️ 从 `@/lib/auth` 导入而非内联：`lib/auth.ts` 不 import 本文件，**无循环依赖**。
 *
 * 注意：EventSource 原生不支持自定义 header，SSE 场景请用本文件 `fetchRaw` 做流式读取
 * （参考 services/knowledge.ts uploadDocument）。
 */

import { bearerHeaders, handleAuthFailure, tryRefreshOnce } from "@/lib/auth";
import { TIMEOUT_REASON } from "./errors";

// ── 错误模型 ──────────────────────────────────────────────────

export class ApiError extends Error {
  /**
   * @param message 面向用户 / 日志的文案（优先取后端 `detail`）
   * @param status  HTTP 状态码
   * @param detail  后端原始 `detail`，供排查用；**不要**直接渲染
   * @param code    后端业务错误码（P0-1b）。后端尚未定义真实码时为 `undefined`，
   *                此时交由 `@/api/errors` 按状态码兜底翻译，调用方无需分支。
   */
  constructor(
    message: string,
    public status: number,
    public detail?: unknown,
    public code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** 同一写操作的原始幂等键，供冲突后查询服务端状态。 */
  idempotencyKey?: string;
}

/** 将 HTTP/SSE 的统一错误封套转成同一个 ApiError。 */
export function apiErrorFromEnvelope(payload: unknown, status = 0): ApiError {
  const body = payload && typeof payload === "object" ? payload as Record<string, unknown> : {};
  const detail = body.detail && typeof body.detail === "object" ? body.detail as Record<string, unknown> : body;
  const message = typeof detail.message === "string" ? detail.message : "操作失败，请稍后重试";
  const code = typeof detail.code === "string" ? detail.code : undefined;
  return new ApiError(message, status, detail, code);
}

export interface MutationRequestOptions extends Omit<RequestOptions, "body" | "method"> {
  /** 逻辑写操作名；同一操作在途时共享请求和幂等键。 */
  operation: string;
  body?: unknown;
  method?: string;
  /** 恢复原操作时显式复用服务端返回的键。 */
  idempotencyKey?: string;
}

const mutationInFlight = new Map<string, Promise<unknown>>();
const mutationRawInFlight = new Map<string, Promise<Response>>();

function stableSerialize(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableSerialize).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${stableSerialize(record[key])}`).join(",")}}`;
}

export function createIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `idem-${Date.now()}-${Math.random().toString(36).slice(2, 14)}`;
}

export interface MutationFetchRawOptions extends RequestInit {
  /** 逻辑写操作名；同一操作在途时共享请求与幂等键。 */
  operation: string;
  /** 恢复原操作时显式复用服务端返回的键。 */
  idempotencyKey?: string;
  /** FormData 等无法安全序列化的请求体使用显式去重指纹。 */
  dedupeKey?: string;
  /** 目标后端，默认 core。 */
  backend?: BackendId;
}

/**
 * 统一原始响应写请求：支持 FormData/SSE 前置请求，并保留 401 刷新语义。
 * 每个调用方拿到独立 Response 副本，避免并发调用共同消费同一个 body 流。
 */
export async function mutationFetchRaw(
  path: string,
  options: MutationFetchRawOptions,
): Promise<Response> {
  const {
    operation,
    idempotencyKey: requestedKey,
    dedupeKey,
    backend = "core",
    ...init
  } = options;
  const method = (init.method ?? "POST").toUpperCase();
  if (method === "GET" || method === "HEAD") {
    throw new TypeError("mutationFetchRaw 只允许写方法");
  }
  const bodyFingerprint = dedupeKey ?? (
    typeof init.body === "string" ? init.body : ""
  );
  const fingerprint = `${operation}:${method}:${path}:${bodyFingerprint}`;
  const existing = mutationRawInFlight.get(fingerprint);
  if (existing) return existing.then((response) => response.clone());

  const idempotencyKey = requestedKey ?? createIdempotencyKey();
  const headers = Object.fromEntries(new Headers(init.headers).entries());
  const promise = fetchRaw(path, {
    ...init,
    method,
    headers: {
      ...headers,
      "Idempotency-Key": idempotencyKey,
    },
  }, backend).finally(() => {
    if (mutationRawInFlight.get(fingerprint) === promise) {
      mutationRawInFlight.delete(fingerprint);
    }
  });
  mutationRawInFlight.set(fingerprint, promise);
  return promise.then((response) => response.clone());
}

/** 统一写请求：同一逻辑操作在途时只发送一次，并注入稳定幂等键。 */
export async function mutationRequest<T = unknown>(
  path: string,
  options: MutationRequestOptions,
): Promise<T> {
  const method = (options.method ?? "POST").toUpperCase();
  if (method === "GET" || method === "HEAD") throw new TypeError("mutationRequest 只允许写方法");
  const bodyText = typeof options.body === "string"
    ? options.body
    : options.body === undefined ? undefined : JSON.stringify(options.body);
  const fingerprint = `${options.operation}:${method}:${path}:${stableSerialize(options.body ?? null)}`;
  const existing = mutationInFlight.get(fingerprint);
  if (existing) return existing as Promise<T>;
  const idempotencyKey = options.idempotencyKey ?? createIdempotencyKey();
  const { operation: _operation, idempotencyKey: _key, body: _body, ...requestOptions } = options;
  const mutationHeaders = Object.fromEntries(new Headers(requestOptions.headers).entries());
  let promise: Promise<T>;
  promise = request<T>(path, {
    ...requestOptions,
    method,
    body: bodyText,
    headers: {
      ...mutationHeaders,
      "Idempotency-Key": idempotencyKey,
    },
  } as RequestOptions).catch((error: unknown) => {
    if (error instanceof ApiError) error.idempotencyKey = idempotencyKey;
    throw error;
  }).finally(() => {
    if (mutationInFlight.get(fingerprint) === promise) mutationInFlight.delete(fingerprint);
  });
  mutationInFlight.set(fingerprint, promise);
  return promise;
}

/**
 * 从响应体里尽力取出业务错误码（P0-1b）。
 *
 * 兼容三种后端写法：顶层 `code`、`detail.code`、`detail.error_code`。
 * 取不到返回 `undefined` —— 由 `@/api/errors` 的降级链兜底，**不抛错**。
 */
function extractErrorCode(data: unknown): string | undefined {
  if (!data || typeof data !== "object") return undefined;
  const body = data as Record<string, unknown>;
  const direct = body.code;
  if (typeof direct === "string" && direct) return direct;
  const detail = body.detail;
  if (detail && typeof detail === "object") {
    const d = detail as Record<string, unknown>;
    for (const key of ["code", "error_code"] as const) {
      const v = d[key];
      if (typeof v === "string" && v) return v;
    }
  }
  return undefined;
}

// ── 后端基址映射（多后端预留） ─────────────────────────────────

/**
 * 逻辑后端标识。当前仅 `core` 真实存在；`business` 为「业务数据迁独立服务」预留槽位。
 * 新增后端：此处加枚举值 + 在 `backendBaseUrl()` 内加一行环境变量读取，调用方无需改动。
 */
export type BackendId = "core" | "business";

function envBase(value: string | undefined): string | undefined {
  const trimmed = (value ?? "").trim();
  return trimmed ? trimmed.replace(/\/$/, "") : undefined;
}

/**
 * 解析逻辑后端对应的基址。
 *
 * 读取时机在**调用时**而非模块加载时 —— 便于测试覆写 env，且 Next 对
 * `process.env.NEXT_PUBLIC_*` 的静态内联同样生效。
 */
export function backendBaseUrl(backend: BackendId = "core"): string {
  const core = envBase(process.env.NEXT_PUBLIC_API_URL) ?? "";
  if (backend === "business") {
    // 未配置独立基址 → 回落 core，保证拆分前后行为一致
    return envBase(process.env.NEXT_PUBLIC_BUSINESS_API_URL) ?? core;
  }
  return core;
}

function joinUrl(path: string, backend: BackendId): string {
  if (path.startsWith("http")) return path;
  const base = backendBaseUrl(backend);
  return `${base}${path.startsWith("/") ? path : `/${path}`}`;
}

// ── 认证判定 ──────────────────────────────────────────────────

/** 登录/刷新/注册等 auth 端点自身不做 401 重试，避免递归 */
function isAuthPath(input: string): boolean {
  // 相对路径与跨网关绝对路径都要覆盖
  return input.startsWith("/api/auth/") || /:\/\/[^/]+\/api\/auth\//.test(input);
}

function buildHeaders(init?: RequestInit): Record<string, string> {
  // 凭据收口（2026-09-16 方案 B）：X-API-Key 由服务端代理路由
  // （app/api/[...path]/route.ts）注入，浏览器不再持有服务级密钥。
  // 旧变量 NEXT_PUBLIC_API_KEY 已废弃，请勿在此引用（会重新泄漏进 bundle）。
  return {
    ...bearerHeaders(),
    ...Object.fromEntries(new Headers(init?.headers).entries()),
  };
}

// ── Response 层 ───────────────────────────────────────────────

/**
 * Response 层请求：返回原始 `Response`，调用方自行解析。
 * 适用：需要流式读取（SSE）、FormData 上传、或需要看响应头/状态的场景。
 *
 * `_retried` 供 401 重试防递归，外部不要传。
 */
export async function fetchRaw(
  path: string,
  init: RequestInit = {},
  backend: BackendId = "core",
  _retried = false,
): Promise<Response> {
  const doFetch = () =>
    fetch(joinUrl(path, backend), { ...init, headers: buildHeaders(init) });

  const res = await doFetch();

  if (res.status === 401 && !_retried && !isAuthPath(path)) {
    if (await tryRefreshOnce()) {
      return fetchRaw(path, init, backend, true);
    }
    handleAuthFailure();
  }

  return res;
}

// ── JSON 层 ───────────────────────────────────────────────────

export interface RequestOptions extends Omit<RequestInit, "signal"> {
  /** 超时毫秒，默认 30000 */
  timeout?: number;
  /** AbortSignal 用于外部取消 */
  signal?: AbortSignal;
  /** 目标后端，默认 `core` */
  backend?: BackendId;
}

const DEFAULT_TIMEOUT = 30_000;

/**
 * 通用 JSON 请求。非 2xx 抛 `ApiError`。
 *
 * `_retried` 供 401 重试防递归，外部不要传。
 */
export async function request<T = unknown>(
  path: string,
  options: RequestOptions = {},
  _retried = false,
): Promise<T> {
  const {
    timeout = DEFAULT_TIMEOUT,
    signal: externalSignal,
    backend = "core",
    ...init
  } = options;

  const controller = new AbortController();
  const timer = setTimeout(
    () => controller.abort(new Error(TIMEOUT_REASON)),
    timeout,
  );

  // 合并外部 signal
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort(externalSignal.reason);
    else
      externalSignal.addEventListener(
        "abort",
        () => controller.abort(externalSignal.reason),
        { once: true },
      );
  }

  try {
    const res = await fetch(joinUrl(path, backend), {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...buildHeaders(init),
      },
      signal: controller.signal,
    });

    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      // 401：先尝试静默刷新，成功则重试一次原请求；失败则跳登录页
      if (res.status === 401 && !_retried && !isAuthPath(path)) {
        if (await tryRefreshOnce()) {
          return request<T>(path, options, true);
        }
        handleAuthFailure();
      }
      // 后端 FastAPI 习惯：detail 字段含错误信息
      const detail = (data as Record<string, unknown>)?.detail;
      const payload = data && typeof data === "object"
        && (["retryable", "handoff_available", "trace_id", "source", "details"] as const)
          .some((key) => key in (data as Record<string, unknown>))
        ? data
        : detail;
      const message =
        (typeof detail === "string" && detail) ||
        (typeof detail === "object" &&
          detail &&
          ((detail as Record<string, string>).error ||
            (detail as Record<string, string>).message)) ||
        res.statusText ||
        `HTTP ${res.status}`;
      throw new ApiError(
        String(message),
        res.status,
        payload,
        extractErrorCode(data),
      );
    }

    return data as T;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * 不抛错的请求：用于"删了也行，删失败也不影响主流程"的场景
 */
export async function requestSilent(
  path: string,
  options: RequestOptions = {},
): Promise<void> {
  try {
    await request(path, options);
  } catch {
    /* ignore */
  }
}
