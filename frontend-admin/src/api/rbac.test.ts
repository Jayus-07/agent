import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/fetcher";
import { request } from "@/lib/fetcher";
import {
  deactivateRbacDepartment,
  listRbacAudit,
  listRbacDepartments,
  listRbacUsers,
  updateRbacDepartment,
  updateRbacUser,
} from "./rbac";

vi.mock("@/lib/fetcher", () => ({
  ApiError: class ApiError extends Error {
    constructor(
      message: string,
      public status: number,
      public detail?: unknown,
      public code?: string,
    ) {
      super(message);
      this.name = "ApiError";
    }
  },
  request: vi.fn(),
}));

describe("RBAC API 客户端", () => {
  beforeEach(() => vi.mocked(request).mockReset());

  it("按后端 query 约定分页搜索用户，并解开 Result 或裸分页响应", async () => {
    vi.mocked(request).mockResolvedValueOnce({
      items: [],
      total: 0,
      page: 2,
      pageSize: 10,
    });

    await expect(
      listRbacUsers({ search: "alice", status: 0, page: 2, pageSize: 10 }),
    ).resolves.toMatchObject({ total: 0, pageSize: 10 });
    expect(request).toHaveBeenCalledWith(
      "/api/sys/rbac/users?search=alice&status=0&page=2&page_size=10",
    );
  });

  it("平台用户 PATCH 只携带平台和资料字段，不接受客服档案字段", async () => {
    vi.mocked(request).mockResolvedValueOnce({
      userId: 7,
      username: "alice",
      platformRole: "editor",
      version: 9,
      csAgent: null,
    });

    await updateRbacUser(7, {
      version: 8,
      platformRole: "editor",
      realName: "Alice",
      email: "alice@example.com",
    });

    expect(request).toHaveBeenCalledWith(
      "/api/sys/rbac/users/7",
      {
        method: "PATCH",
        body: JSON.stringify({
          version: 8,
          platformRole: "editor",
          realName: "Alice",
          email: "alice@example.com",
        }),
      },
    );
    expect(JSON.stringify(vi.mocked(request).mock.calls[0])).not.toContain("csRole");
  });

  it("部门目录可选包含停用项，改名和停用都携带版本号", async () => {
    vi.mocked(request).mockResolvedValueOnce({ items: [] });
    await listRbacDepartments({ includeInactive: true });
    expect(request).toHaveBeenCalledWith(
      "/api/sys/rbac/departments?include_inactive=true",
    );

    vi.mocked(request).mockResolvedValueOnce({ code: "sales", name: "销售部" });
    await updateRbacDepartment("sales", { version: 4, name: "华东销售部" });
    expect(request).toHaveBeenLastCalledWith(
      "/api/sys/rbac/departments/sales",
      { method: "PATCH", body: JSON.stringify({ version: 4, name: "华东销售部" }) },
    );

    vi.mocked(request).mockResolvedValueOnce({ code: "sales", status: 0 });
    await deactivateRbacDepartment("sales", 5);
    expect(request).toHaveBeenLastCalledWith(
      "/api/sys/rbac/departments/sales?version=5",
      { method: "DELETE" },
    );
  });

  it("兼容仅返回已启用部门名称和编码的旧列表响应", async () => {
    vi.mocked(request).mockResolvedValueOnce({
      items: [{ code: "support", name: "客服部" }],
    });

    await expect(listRbacDepartments()).resolves.toEqual([{
      code: "support",
      name: "客服部",
      status: 1,
      version: null,
      departmentType: "general",
      managerUserId: null,
      managerUsername: null,
      managerName: null,
      memberCount: null,
      updatedAt: null,
    }]);
  });

  it("审计请求使用 snake_case query，并保留 camelCase 审计字段", async () => {
    vi.mocked(request).mockResolvedValueOnce({
      code: 200,
      message: "success",
      data: { items: [{ targetUserId: 7 }], total: 1, page: 1, pageSize: 50 },
    });

    await expect(
      listRbacAudit({ page: 1, pageSize: 50, userId: 7 }),
    ).resolves.toMatchObject({ items: [{ targetUserId: 7 }] });
    expect(request).toHaveBeenCalledWith(
      "/api/sys/rbac/audit?page=1&page_size=50&user_id=7",
    );
  });

  it("不吞掉 409/403，让页面按 status 提供冲突和权限操作", async () => {
    const conflict = new ApiError("用户版本冲突，请刷新后重试", 409);
    vi.mocked(request).mockRejectedValueOnce(conflict);
    await expect(
      updateRbacUser(7, { version: 8, platformRole: "admin" }),
    ).rejects.toMatchObject({ status: 409 });

    const forbidden = new ApiError("仅 admin 可访问 RBAC 管理端", 403);
    vi.mocked(request).mockRejectedValueOnce(forbidden);
    await expect(listRbacUsers()).rejects.toMatchObject({ status: 403 });
  });
});
