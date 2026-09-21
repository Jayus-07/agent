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

/** 登录/刷新回写的权限摘要；不包含 token、密码或 refresh 凭据。 */
export interface UserInfo {
  userId?: string | number;
  username?: string;
  realName?: string;
  roles?: string[];
  platformRole?: string;
  tenantId?: string | null;
  csRole?: "agent" | "supervisor" | null;
  [key: string]: unknown;
}

export interface QueryClientLike {
  clear: () => void;
}

export interface LoginResult {
  token: string;
  expiresIn?: number;
  userInfo?: UserInfo | null;
}

let accessToken: string | null = null;
let loaded = false;
let registeredQueryClient: QueryClientLike | null = null;

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

export function getCachedUser(): UserInfo | null {
  if (!isBrowser()) return null;
  try {
    const raw = sessionStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as UserInfo | null) : null;
  } catch {
    return null;
  }
}

/**
 * 允许根布局把真实 QueryClient 注册给认证模块。
 * Sidebar 在 QueryClientProvider 外时也能安全调用 logout；若调用方在
 * Provider 内，可把 client 直接传给 logout，避免依赖全局单例。
 */
export function registerQueryClient(queryClient: QueryClientLike): () => void {
  registeredQueryClient = queryClient;
  return () => {
    if (registeredQueryClient === queryClient) registeredQueryClient = null;
  };
}

function writeUserInfo(userInfo: UserInfo | null | undefined): void {
  if (!isBrowser()) return;
  try {
    sessionStorage.setItem(USER_KEY, JSON.stringify(userInfo ?? null));
  } catch {
    /* ignore */
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
      writeUserInfo(data.userInfo);
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
        // 角色/租户/客服绑定可能在当前 access token 生命周期内被管理员修改；
        // refresh 必须以服务端完整 userInfo 覆盖旧缓存，而不是只换 token。
        if (Object.prototype.hasOwnProperty.call(data, "userInfo")) {
          writeUserInfo(data.userInfo);
        }
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
export async function logout(queryClient?: QueryClientLike): Promise<void> {
  const token = getAccessToken();
  try {
    await fetch(`${API_BASE}/api/auth/logout`, {
      method: "POST",
      credentials: "include",
      ...(token ? { headers: { Authorization: `Bearer ${token}` } } : {}),
    });
  } catch {
    /* ignore */
  } finally {
    // 网络失败也必须清态，避免旧 token 留在内存或 sessionStorage。
    setAccessToken(null);
    try {
      if (isBrowser()) {
        sessionStorage.removeItem(USER_KEY);
        sessionStorage.removeItem(EXPIRED_KEY);
      }
    } catch {
      /* ignore */
    }
    const clients = [queryClient, registeredQueryClient].filter(
      (client): client is QueryClientLike => Boolean(client),
    );
    const seen = new Set<QueryClientLike>();
    for (const client of clients) {
      if (seen.has(client)) continue;
      seen.add(client);
      try {
        client.clear();
      } catch {
        /* cache 清理不是跳转失败的理由 */
      }
    }
    // csAgentWs 监听此事件，立即关闭连接并阻止退避重连。
    if (isBrowser()) window.dispatchEvent(new Event("agent:logout"));
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

/* ────────────────────────────────────────────────────────────
 * 角色工具（2026-09-16）：roles 来自 login/refresh 响应的 userInfo.roles
 * （后端 auth.users.role，viewer/editor/admin）。序与后端 _ROLE_RANK 同构。
 * 仅用于 UI 呈现（导航显隐）；真正的权限判定在后端（403 兜底），
 * 角色变更在下次登录/refresh 后进入本地缓存。
 * ──────────────────────────────────────────────────────────── */

const ROLE_RANK: Record<string, number> = { viewer: 0, editor: 1, admin: 2 }
export type RoleName = keyof typeof ROLE_RANK

/** 当前登录用户的角色列表（未登录/无信息返回空数组） */
export function getRoles(): string[] {
  const info = (getCachedUser() ?? {}) as { roles?: unknown }
  return Array.isArray(info.roles)
    ? info.roles.filter((r): r is string => typeof r === "string")
    : []
}

/** 是否拥有不低于 min 的角色（多角色取最高，未登录恒 false） */
export function atLeast(min: RoleName): boolean {
  const need = ROLE_RANK[min] ?? 0
  return getRoles().some((r) => (ROLE_RANK[r] ?? -1) >= need)
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
 * 开发者注册 + 记住用户名（只记用户名，密码永不落 localStorage——
 * 该存储暴露于 XSS 时可被读取；历史上曾明文存过密码
 * （agent.saved_credentials），getSavedUsername 读取时顺带清除残留）
 * ──────────────────────────────────────────────────────────── */

const USERNAME_KEY = "agent.saved_username";
// 历史版本的明文凭据键（{username, password}），读到即清除
const LEGACY_CREDS_KEY = "agent.saved_credentials";
// 记住密码（2026-09-17 用户明确要求）：Base64 仅防肉眼直读，不是加密——
// localStorage 暴露于 XSS 时可还原，安全语义等同明文，仅限本机演示环境使用
const PASSWORD_KEY = "agent.saved_password";

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

/** 清除历史明文凭据残留（幂等，随 getSavedUsername/clear 一并触发） */
function purgeLegacyCredentials(): void {
  try {
    localStorage.removeItem(LEGACY_CREDS_KEY);
  } catch {
    /* ignore */
  }
}

/** 记住用户名：登录成功后按需调用（只记用户名，不记密码） */
export function saveUsername(username: string): void {
  if (!isBrowser()) return;
  try {
    localStorage.setItem(USERNAME_KEY, username);
  } catch {
    /* ignore */
  }
}

export function getSavedUsername(): string | null {
  if (!isBrowser()) return null;
  purgeLegacyCredentials();
  try {
    return localStorage.getItem(USERNAME_KEY) || null;
  } catch {
    return null;
  }
}

/** 忘记此用户名（下次登录改回手动输入） */
export function clearSavedUsername(): void {
  if (!isBrowser()) return;
  purgeLegacyCredentials();
  try {
    localStorage.removeItem(USERNAME_KEY);
  } catch {
    /* ignore */
  }
}

/**
 * 记住账号和密码（登录成功后按需调用）。
 * encodeURIComponent 包一层再 btoa，避免非 Latin1 字符（中文密码）炸 btoa。
 */
export function saveCredentials(username: string, password: string): void {
  if (!isBrowser()) return;
  try {
    localStorage.setItem(USERNAME_KEY, username);
    localStorage.setItem(PASSWORD_KEY, btoa(encodeURIComponent(password)));
  } catch {
    /* ignore */
  }
}

/** 读取记住的密码（无存档返回 null） */
export function getSavedPassword(): string | null {
  if (!isBrowser()) return null;
  try {
    const raw = localStorage.getItem(PASSWORD_KEY);
    return raw ? decodeURIComponent(atob(raw)) : null;
  } catch {
    return null;
  }
}

/** 清除记住的密码（记住密码取消勾选时调用） */
export function clearSavedPassword(): void {
  if (!isBrowser()) return;
  try {
    localStorage.removeItem(PASSWORD_KEY);
  } catch {
    /* ignore */
  }
}
