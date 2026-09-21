import { ApiError, request } from "@/lib/fetcher";

export type PlatformRole = "viewer" | "editor" | "admin";
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
  platformRole?: PlatformRole;
  csRole?: CsRole | null;
  maxConversations?: number;
  enabled?: boolean;
  accepting?: boolean;
}

export interface RbacAuditItem {
  id: number;
  operator: string;
  actor: string;
  actorUserId: number | null;
  target: string;
  targetUserId: number;
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

export async function listRbacUsers(params: {
  search?: string;
  page?: number;
  pageSize?: number;
} = {}): Promise<RbacUserPage> {
  const page = params.page ?? 1;
  const pageSize = params.pageSize ?? 20;
  const body = await request(
    `/api/sys/rbac/users${queryString([
      ["search", params.search?.trim()],
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
