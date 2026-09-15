/**
 * lib/auth.ts — 登录态管理（对接网关 + auth-service JWT 体系）
 *
 * 契约（docs/auth/02-详细架构设计.md + Enterprise_OA common-api）：
 * - POST /api/auth/login  {username, password, deviceId?} → Result{data: LoginVO}
 *   LoginVO: { token, refreshToken(始终为 null，改走 HttpOnly Cookie),
 *              tokenType: "Bearer", expiresIn(ms), userInfo }
 * - POST /api/auth/refresh 凭 HttpOnly Cookie refresh_token 换新 token（令牌轮换）
 * - POST /api/auth/logout 吊销 refresh_token 并写入网关黑名单
 *
 * Access token 策略：模块内存为主 + sessionStorage 兜底（刷新页面不丢，
 * 关闭标签页即失效；refresh_token 本身就在 HttpOnly Cookie 里，可静默续期）。
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

const TOKEN_KEY = "agent.access_token";
const USER_KEY = "agent.user_info";
const DEVICE_KEY = "agent.device_id";
const EXPIRED_KEY = "agent.session_expired";

export interface LoginResult {
  token: string;
  expiresIn?: number;
  userInfo?: { userId?: string; username?: string; [k: string]: unknown } | null;
}

let accessToken: string | null = null;
let loaded = false;

function isBrowser(): boolean {
  return typeof window !== "undefined";
}

/** 模块级 token 初始化（首次访问时从 sessionStorage 恢复） */
function ensureLoaded(): void {
  if (loaded || !isBrowser()) return;
  try {
    accessToken = sessionStorage.getItem(TOKEN_KEY);
  } catch {
    accessToken = null;
  }
  loaded = true;
}

export function getAccessToken(): string | null {
  ensureLoaded();
  return accessToken;
}

function setAccessToken(token: string | null): void {
  accessToken = token;
  if (!isBrowser()) return;
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* 隐私模式等场景下静默降级为纯内存 */
  }
}

export function getCachedUser(): LoginResult["userInfo"] {
  if (!isBrowser()) return null;
  try {
    const raw = sessionStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as LoginResult["userInfo"]) : null;
  } catch {
    return null;
  }
}

/** 设备 ID：前端生成并持久化，登录时携带（旧系统风控字段） */
export function getDeviceId(): string {
  if (!isBrowser()) return "";
  try {
    let id = localStorage.getItem(DEVICE_KEY);
    if (!id) {
      id =
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : `dev-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
      localStorage.setItem(DEVICE_KEY, id);
    }
    return id;
  } catch {
    return "";
  }
}

/** 解析旧系统 Result 包裹：{code, message, data, timestamp} */
function unwrapResult<T>(body: { code?: number; message?: string; data?: T } | null): T {
  if (body && typeof body.code === "number" && body.code !== 200) {
    throw new Error(body.message || `认证失败（code=${body.code}）`);
  }
  if (!body || body.data == null) {
    throw new Error(body?.message || "认证响应缺少 data 字段");
  }
  return body.data;
}

/** 登录：成功后保存 access token 与用户信息（refresh_token 由后端种 HttpOnly Cookie） */
export async function login(username: string, password: string): Promise<LoginResult> {
  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ username, password, deviceId: getDeviceId() }),
  });
  const body = await res.json().catch(() => null);
  const data = unwrapResult<LoginResult & { refreshToken?: string | null }>(body);
  if (!data.token) throw new Error("登录响应缺少 token");
  setAccessToken(data.token);
  if (isBrowser()) {
    try {
      sessionStorage.setItem(USER_KEY, JSON.stringify(data.userInfo ?? null));
      sessionStorage.removeItem(EXPIRED_KEY);
    } catch {
      /* ignore */
    }
  }
  return data;
}

/** 刷新（single-flight）：并发 401 只触发一次 refresh 请求 */
let refreshInFlight: Promise<boolean> | null = null;

export function tryRefreshOnce(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/auth/refresh`, {
          method: "POST",
          credentials: "include",
        });
        if (!res.ok) return false;
        const body = await res.json().catch(() => null);
        const data = unwrapResult<LoginResult>(body);
        if (!data.token) return false;
        setAccessToken(data.token);
        return true;
      } catch {
        return false;
      } finally {
        // 微任务结束后清空，避免把 single-flight 标记泄漏给下一轮 401
        setTimeout(() => {
          refreshInFlight = null;
        }, 0);
      }
    })();
  }
  return refreshInFlight;
}

/** 登出：尽力通知后端吊销，无论成败都清本地登录态 */
export async function logout(): Promise<void> {
  const token = getAccessToken();
  try {
    await fetch(`${API_BASE}/api/auth/logout`, {
      method: "POST",
      credentials: "include",
      ...(token ? { headers: { Authorization: `Bearer ${token}` } } : {}),
    });
  } catch {
    /* ignore */
  }
  setAccessToken(null);
  if (isBrowser()) {
    try {
      sessionStorage.removeItem(USER_KEY);
    } catch {
      /* ignore */
    }
  }
}

/**
 * 认证彻底失败（refresh 也失败）：清态 + 打过期标记 + 跳登录页。
 * 登录页读取 EXPIRED_KEY 显示"登录已过期"，登录成功后跳回 redirect 参数页。
 */
export function handleAuthFailure(): void {
  if (!isBrowser()) return;
  setAccessToken(null);
  try {
    sessionStorage.setItem(EXPIRED_KEY, "1");
  } catch {
    /* ignore */
  }
  const { pathname, search } = window.location;
  if (pathname.startsWith("/login")) return;
  const redirect = encodeURIComponent(pathname + search);
  window.location.assign(`/login?redirect=${redirect}`);
}

/** 请求头注入：业务请求统一带 Bearer（X-API-Key 由 @/api/client 保留） */
export function bearerHeaders(): Record<string, string> {
  const token = getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** 登录页用：是否有"被踢回"标记 */
export function consumeExpiredFlag(): boolean {
  if (!isBrowser()) return false;
  try {
    const v = sessionStorage.getItem(EXPIRED_KEY);
    sessionStorage.removeItem(EXPIRED_KEY);
    return v === "1";
  } catch {
    return false;
  }
}

/* ────────────────────────────────────────────────────────────
 * 开发者注册 + 记住凭据（仅限内部工具：凭据明文存 localStorage，
 * 换取"一键登录"体验；该存储暴露于 XSS 时可被读取，生产对外环境勿开启）
 * ──────────────────────────────────────────────────────────── */

const CREDS_KEY = "agent.saved_credentials";

export interface RegisterResult {
  userId?: number;
  username?: string;
  realName?: string;
  message?: string;
}

/**
 * 注册开发者账号：走网关 /api/sys/** → system-service（context-path /system）
 * 用户名 3-20 字符、密码 6-20 字符（后端 @Size 校验）。
 */
export async function register(
  username: string,
  password: string,
  confirmPassword: string,
  realName?: string,
): Promise<RegisterResult> {
  const res = await fetch(`${API_BASE}/api/sys/users/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username,
      password,
      confirmPassword,
      ...(realName ? { realName } : {}),
    }),
  });
  const body = await res.json().catch(() => null);
  // Result 包裹（同登录）：code!=200 抛 message
  return unwrapResult<RegisterResult>(body);
}

/** 记住凭据：注册/登录成功后按需调用 */
export function saveCredentials(username: string, password: string): void {
  if (!isBrowser()) return;
  try {
    localStorage.setItem(CREDS_KEY, JSON.stringify({ username, password }));
  } catch {
    /* ignore */
  }
}

export function getSavedCredentials(): { username: string; password: string } | null {
  if (!isBrowser()) return null;
  try {
    const raw = localStorage.getItem(CREDS_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { username?: string; password?: string };
    if (parsed?.username && parsed?.password) {
      return { username: parsed.username, password: parsed.password };
    }
    return null;
  } catch {
    return null;
  }
}

/** 忘记此账号（一键登录失效，改回手动输入） */
export function clearSavedCredentials(): void {
  if (!isBrowser()) return;
  try {
    localStorage.removeItem(CREDS_KEY);
  } catch {
    /* ignore */
  }
}
