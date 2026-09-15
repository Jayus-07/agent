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
  return {
    ...(process.env.NEXT_PUBLIC_API_KEY
      ? { "X-API-Key": process.env.NEXT_PUBLIC_API_KEY }
      : {}),
    ...bearerHeaders(),
    ...((init?.headers as Record<string, string>) || {}),
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
        detail,
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
