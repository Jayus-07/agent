/**
 * 底层 fetch 抽象：统一 JSON 解析、错误处理、超时
 * 所有 API 模块都基于这个，避免重复
 */

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface RequestOptions extends Omit<RequestInit, "signal"> {
  /** 超时毫秒，默认 30000 */
  timeout?: number;
  /** AbortSignal 用于外部取消 */
  signal?: AbortSignal;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
const DEFAULT_TIMEOUT = 30_000;

function joinUrl(path: string): string {
  if (path.startsWith("http")) return path;
  return `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
}

/**
 * 通用 JSON 请求
 */
export async function request<T = unknown>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { timeout = DEFAULT_TIMEOUT, signal: externalSignal, ...init } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error("Request timeout")), timeout);

  // 合并外部 signal
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort(externalSignal.reason);
    else externalSignal.addEventListener("abort", () => controller.abort(externalSignal.reason), { once: true });
  }

  try {
    const res = await fetch(joinUrl(path), {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...init.headers,
      },
      signal: controller.signal,
    });

    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      // 后端 FastAPI 习惯：detail 字段含错误信息
      const detail = data?.detail;
      const message =
        (typeof detail === "string" && detail) ||
        (typeof detail === "object" && detail && (detail.error || detail.message)) ||
        res.statusText ||
        `HTTP ${res.status}`;
      throw new ApiError(String(message), res.status, detail);
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