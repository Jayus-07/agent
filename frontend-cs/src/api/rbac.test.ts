import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/fetcher";
import { request } from "@/lib/fetcher";
import { listRbacAudit, listRbacUsers, updateRbacUser } from "./rbac";

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
      listRbacUsers({ search: "alice", page: 2, pageSize: 10 }),
    ).resolves.toMatchObject({ total: 0, pageSize: 10 });
    expect(request).toHaveBeenCalledWith(
      "/api/sys/rbac/users?search=alice&page=2&page_size=10",
    );
  });

  it("PATCH 携带当前 version 与 camelCase 字段，不接受浏览器传 agentId", async () => {
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
      csRole: "agent",
      maxConversations: 4,
      enabled: true,
      accepting: false,
    });

    expect(request).toHaveBeenCalledWith(
      "/api/sys/rbac/users/7",
      {
        method: "PATCH",
        body: JSON.stringify({
          version: 8,
          platformRole: "editor",
          csRole: "agent",
          maxConversations: 4,
          enabled: true,
          accepting: false,
        }),
      },
    );
    expect(JSON.stringify(vi.mocked(request).mock.calls[0])).not.toContain(
      "agentId",
    );
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
