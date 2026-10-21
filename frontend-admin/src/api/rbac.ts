import { ApiError, request } from "@/lib/fetcher";

export type PlatformRole = "viewer" | "editor" | "admin" | "super_admin";
export type CsRole = "agent" | "supervisor";

export interface RbacAgent {
  agentId: string;
  displayName: string;
  role: CsRole;
  maxConversations: number;
  enabled: boolean;
  accepting: boolean;
}

export interface RbacUser {
  userId: number;
  username: string;
  realName: string;
  email?: string;
  dept: string;
  platformRole: PlatformRole;
  status: number;
  version: number;
  sessionCount: number;
  csAgent: RbacAgent | null;
}

export interface RbacUserPage {
  items: RbacUser[];
  total: number;
  page: number;
  pageSize: number;
}

export interface RbacUserPatch {
  version: number;
  realName?: string;
  email?: string;
  platformRole?: PlatformRole;
  status?: number;
  /** 部门 code（授权属性）：空串=清空；非空须为本租户 active 部门（后端校验） */
  dept?: string;
  csRole?: CsRole | null;
  maxConversations?: number;
  enabled?: boolean;
  accepting?: boolean;
}

/** 部门主数据（041_auth_departments；管理端部门下拉唯一数据源，禁前端写死） */
export interface RbacDepartment {
  code: string;
  name: string;
  status: number;
  /** 旧后端尚无部门版本号时为 null；前端应禁用编辑/状态变更。 */
  version: number | null;
  memberCount: number | null;
  updatedAt: string | null;
}

export interface RbacCreateDepartmentBody {
  code: string;
  name: string;
}

export interface RbacDepartmentPatch {
  version: number;
  name?: string;
  status?: 0 | 1;
}

export interface RbacAuditItem {
  id: number;
  operator: string;
  actor: string;
  actorUserId: number | null;
  actorUsername: string | null;
  target: string;
  targetUserId: number;
  targetUsername: string | null;
  action: string;
  oldPlatformRole: PlatformRole | null;
  newPlatformRole: PlatformRole | null;
  csChanges: Record<string, unknown>;
  beforeState?: Record<string, unknown>;
  afterState?: Record<string, unknown>;
  result: string;
  createdAt: string | null;
}

export interface RbacAuditPage {
  items: RbacAuditItem[];
  total: number;
  page: number;
  pageSize: number;
}

interface Result<T> {
  code: number;
  message?: string;
  data: T;
}

function isResult<T>(value: unknown): value is Result<T> {
  return (
    !!value &&
    typeof value === "object" &&
    typeof (value as { code?: unknown }).code === "number" &&
    "data" in value
  );
}

/** 同时兼容管理端 Result 壳和当前 RBAC 路由的裸 JSON 资源。 */
function unwrap<T>(value: unknown): T {
  if (!isResult<T>(value)) return value as T;
  if (value.code !== 200) {
    // 正常 HTTP 失败由 request 抛 ApiError；此分支覆盖网关把业务失败包在 2xx 的旧契约。
    throw new ApiError(value.message || "RBAC 请求失败", value.code, value);
  }
  return value.data;
}

function queryString(entries: Array<[string, string | number | undefined]>): string {
  const query = new URLSearchParams();
  for (const [key, value] of entries) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  const encoded = query.toString();
  return encoded ? `?${encoded}` : "";
}

function normalizeDepartment(
  department: Partial<RbacDepartment> & Pick<RbacDepartment, "code" | "name">,
): RbacDepartment {
  return {
    code: department.code,
    name: department.name,
    // 舊 API 只返回启用部门的 code/name，保持其兼容语义。
    status: department.status === 0 ? 0 : 1,
    version: Number.isInteger(department.version) ? department.version! : null,
    memberCount: Number.isInteger(department.memberCount) ? department.memberCount! : null,
    updatedAt: typeof department.updatedAt === "string" ? department.updatedAt : null,
  };
}

export async function listRbacDepartments(options: {
  includeInactive?: boolean;
} = {}): Promise<RbacDepartment[]> {
  const body = await request(
    `/api/sys/rbac/departments${queryString([
      ["include_inactive", options.includeInactive ? "true" : undefined],
    ])}`,
  );
  return unwrap<{ items: Array<Partial<RbacDepartment> & Pick<RbacDepartment, "code" | "name">> }>(body)
    .items.map(normalizeDepartment);
}

export async function updateRbacDepartment(
  code: string,
  patch: RbacDepartmentPatch,
): Promise<RbacDepartment> {
  const body = await request(`/api/sys/rbac/departments/${encodeURIComponent(code)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
  return normalizeDepartment(unwrap<Partial<RbacDepartment> & Pick<RbacDepartment, "code" | "name">>(body));
}

export async function deactivateRbacDepartment(
  code: string,
  version: number,
): Promise<RbacDepartment> {
  const body = await request(
    `/api/sys/rbac/departments/${encodeURIComponent(code)}${queryString([["version", version]])}`,
    { method: "DELETE" },
  );
  return normalizeDepartment(unwrap<Partial<RbacDepartment> & Pick<RbacDepartment, "code" | "name">>(body));
}

export async function createRbacDepartment(
  payload: RbacCreateDepartmentBody,
): Promise<RbacDepartment> {
  const body = await request(`/api/sys/rbac/departments`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return normalizeDepartment(unwrap<Partial<RbacDepartment> & Pick<RbacDepartment, "code" | "name">>(body));
}

export async function listRbacUsers(params: {
  search?: string;
  status?: number;
  page?: number;
  pageSize?: number;
} = {}): Promise<RbacUserPage> {
  const page = params.page ?? 1;
  const pageSize = params.pageSize ?? 20;
  const body = await request(
    `/api/sys/rbac/users${queryString([
      ["search", params.search?.trim()],
      ["status", params.status],
      ["page", page],
      ["page_size", pageSize],
    ])}`,
  );
  return unwrap<RbacUserPage>(body);
}

export async function updateRbacUser(
  userId: number,
  patch: RbacUserPatch,
): Promise<RbacUser> {
  const body = await request(`/api/sys/rbac/users/${userId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
  return unwrap<RbacUser>(body);
}

export async function listRbacAudit(params: {
  page?: number;
  pageSize?: number;
  userId?: number;
} = {}): Promise<RbacAuditPage> {
  const page = params.page ?? 1;
  const pageSize = params.pageSize ?? 50;
  const body = await request(
    `/api/sys/rbac/audit${queryString([
      ["page", page],
      ["page_size", pageSize],
      ["user_id", params.userId],
    ])}`,
  );
  return unwrap<RbacAuditPage>(body);
}

// ── P6 用户生命周期（2026-09-21）─────────────────────────────

export interface RbacUserDetail {
  userId: number;
  username: string;
  realName: string;
  email: string;
  dept: string;
  tenantId: string;
  platformRoles: PlatformRole[];
  csRoles: CsRole[];
  csAgent: RbacAgent | null;
  status: number;
  version: number;
  mustChangePassword: boolean;
  createdAt: string | null;
  lastLoginAt: string | null;
  activeSessionCount: number;
}

export interface RbacCreateUserBody {
  username: string;
  realName?: string;
  dept?: string;
  email?: string;
  platformRole?: PlatformRole;
  csRole?: CsRole | null;
}

export interface RbacCreateUserResult {
  userId: number;
  username: string;
  platformRole: PlatformRole;
  mustChangePassword: boolean;
  /** 临时密码明文（仅创建响应中出现一次） */
  tempPassword: string;
}

export interface RbacResetPasswordResult {
  userId: number;
  username: string;
  mustChangePassword: boolean;
  revokedSessionCount: number;
  tempPassword: string;
}

export interface RbacForceLogoutResult {
  userId: number;
  revokedSessionCount: number;
}

export async function createRbacUser(
  body: RbacCreateUserBody,
): Promise<RbacCreateUserResult> {
  const raw = await request(`/api/sys/rbac/users`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  return unwrap<RbacCreateUserResult>(raw);
}

export async function getRbacUserDetail(userId: number): Promise<RbacUserDetail> {
  const raw = await request(`/api/sys/rbac/users/${userId}`);
  return unwrap<RbacUserDetail>(raw);
}

export async function resetRbacUserPassword(
  userId: number,
): Promise<RbacResetPasswordResult> {
  const raw = await request(`/api/sys/rbac/users/${userId}/reset-password`, {
    method: "POST",
  });
  return unwrap<RbacResetPasswordResult>(raw);
}

export async function forceLogoutRbacUser(
  userId: number,
): Promise<RbacForceLogoutResult> {
  const raw = await request(`/api/sys/rbac/users/${userId}/force-logout`, {
    method: "POST",
  });
  return unwrap<RbacForceLogoutResult>(raw);
}
