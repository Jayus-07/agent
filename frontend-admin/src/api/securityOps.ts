/**
 * 安全运营业务 API（方案 A 配套运营面，2026-09-16）
 *
 * 数据源：FastAPI `/api/sys/security/*`（见 backend/app/api/routes/auth_local.py）
 * - overview：灰度开关状态 + 敏感端点清单（只读）
 * - sessions：在线会话列表（扫 Redis auth:session:*）
 * - DELETE sessions/{uid}/{jti}：强制下线（删会话键，enforce 下即刻生效）
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

export interface SessionRow {
  key: string;
  userId: number;
  jti: string;
  ttlSeconds: number;
  username: string | null;
  realName: string | null;
  role: string | null;
}

export interface SessionList {
  sessions: SessionRow[];
  redisAvailable: boolean;
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

/** DELETE /sys/security/sessions/{uid}/{jti} — 强制下线 */
export async function forceLogout(userId: number, jti: string): Promise<{ revoked: boolean }> {
  const res = await request<Result<{ revoked: boolean; userId: number; jti: string }>>(
    `/api/sys/security/sessions/${userId}/${jti}`,
    { method: "DELETE" },
  );
  return res.data;
}

/** PUT /sys/config/{key} — 覆盖写入灰度开关（免重启，本实例即时生效） */
export async function updateGuardMode(
  configKey: string,
  value: string,
): Promise<{ key: string; old: string | null; new: string }> {
  const res = await request<Result<{ key: string; old: string | null; new: string }>>(
    `/api/sys/config/${configKey}`,
    { method: "PUT", body: JSON.stringify({ value }) },
  );
  return res.data;
}
