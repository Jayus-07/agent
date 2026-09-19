/**
 * 安全运营业务 API（方案 A 配套运营面，2026-09-16）
 *
 * 数据源：FastAPI `/api/sys/security/*`（见 backend/app/api/routes/auth_local.py）
 * - overview：灰度开关状态 + 敏感端点清单（只读）
 * - sessions：在线会话列表（2026-09-19 会话实体改造：DB 口径，一行 = 一次设备登录）
 * - DELETE sessions/{sessionId}：强制下线（撤会话实体 + 家族 refresh + Redis 闸键）
 */
import { request } from "@/lib/fetcher";

// ── 类型 ──────────────────────────────────────────────

export interface GuardModeInfo {
  mode: string | null;
  scope: string;
  note: string;
  /** 2026-09-16 动态化：生效来源 db 覆盖 / env-default / deployment */
  source?: string;
  /** 合法值白名单（非空 = 该开关可在线切换；部署层开关不返回） */
  allowed?: string[];
  configKey?: string;
  updatedAt?: string | null;
  updatedBy?: string | null;
}

export interface SensitiveEndpoint {
  path: string;
  methods: string[];
  guard: string;
  source: "runtime" | "curated";
}

export interface SecurityOverview {
  modes: {
    jwtSessionGuard: GuardModeInfo;
    sensitiveApiGuard: GuardModeInfo;
    gatewaySessionCheck: GuardModeInfo;
  };
  endpoints: SensitiveEndpoint[];
  actor: string;
}

/** 在线会话（一次设备登录 = refresh token family，轮换/多标签不新增行） */
export interface SessionRow {
  sessionId: string;
  userId: number;
  username: string | null;
  realName: string | null;
  role: string | null;
  /** 登录时前端上报的 deviceId（localStorage UUID），空 = 未上报 */
  device: string;
  /** 登录时 User-Agent 原文（截断 256） */
  userAgent: string;
  ip: string;
  createdAt: string;
  lastActiveAt: string;
  /** 会话过期时间 = 家族当前 refresh token 的 expires_at（随轮换滑动续期） */
  expiresAt: string;
}

export interface SessionList {
  sessions: SessionRow[];
}

/**
 * PUT /sys/config/{key} 的响应 —— **裸 dict，无 Result 壳**。
 *
 * 这是本文件里唯一一个不带壳的端点（同文件其余走 `/sys/security/*` 的壳）。
 * 顶层字段由后端 `sys_config_admin.py:51` 决定，并被
 * `backend/tests/api/test_sys_config_admin.py:177` 锁定为顶层 `old`/`new`，
 * 故前端不得按 `Result<T>.data` 取（见 `updateGuardMode` 注释）。
 */
export interface GuardModeUpdateResult {
  key: string;
  /** 此前无 DB 覆盖时为 null（表示生效的是 env 值） */
  old: string | null;
  new: string;
  changedBy: string;
}

// ── 后端 Result 壳 ────────────────────────────────────

interface Result<T> {
  code: number;
  message: string;
  data: T;
}

// ── API ───────────────────────────────────────────────

/** GET /sys/security/overview — 灰度开关 + 敏感端点清单 */
export async function getSecurityOverview(): Promise<SecurityOverview> {
  const res = await request<Result<SecurityOverview>>("/api/sys/security/overview");
  return res.data;
}

/** GET /sys/security/sessions — 在线会话列表 */
export async function getSessions(): Promise<SessionList> {
  const res = await request<Result<SessionList>>("/api/sys/security/sessions");
  return res.data;
}

/** DELETE /sys/security/sessions/{sessionId} — 按 session 强制下线 */
export async function forceLogout(sessionId: string): Promise<{ revoked: boolean; userId: number }> {
  const res = await request<Result<{ revoked: boolean; userId: number; sessionId: string }>>(
    `/api/sys/security/sessions/${sessionId}`,
    { method: "DELETE" },
  );
  return res.data;
}

/**
 * PUT /sys/config/{key} — 覆盖写入灰度开关（免重启，本实例即时生效）
 *
 * ⚠️ 响应是**裸 dict**，**没有 Result 壳** —— 与同前缀 `/sys/security/*` 不同。
 * `request<T>` 返回的就是响应体本身（`client.ts` 不解包），所以这里**直接 return**，
 * 绝不能写 `res.data`：那恒为 `undefined`，调用方 `const { old } = ...` 解构即抛
 * `TypeError: Cannot destructure property 'old' of 'undefined'`。
 *
 * 历史 bug（2026-09-19 修）：此前按 `Result<...>` 取 `.data`，导致 admin 切换守卫开关时
 * 界面显示「切换失败」，**但后端已写库并落审计**（且因抛出而跳过列表刷新）——典型的假失败。
 * 两套响应形态并存是根因，故新端点一律裸 dict（决策见
 * docs/model-config-admin-ui-design.md §1.1 / §5.7）。
 */
export async function updateGuardMode(
  configKey: string,
  value: string,
): Promise<GuardModeUpdateResult> {
  return await request<GuardModeUpdateResult>(
    `/api/sys/config/${configKey}`,
    { method: "PUT", body: JSON.stringify({ value }) },
  );
}
