import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import {
  getCachedUser,
  getAccessToken,
  login,
  logout,
  tryRefreshOnce,
} from "./auth";

beforeAll(() => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
});

function result(data: unknown) {
  return new Response(JSON.stringify({ code: 200, message: "success", data }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("auth RBAC session contract", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("refresh 写回完整 userInfo，而不只更新 access token", async () => {
    sessionStorage.setItem("agent.access_token", "old-token");
    sessionStorage.setItem(
      "agent.user_info",
      JSON.stringify({ userId: 7, roles: ["admin"] }),
    );
    expect(getAccessToken()).toBe("old-token");
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      result({
        token: "new-token",
        userInfo: {
          userId: 7,
          username: "alice",
          roles: ["editor"],
          platformRole: "editor",
          tenantId: "tenant-a",
          csRole: "supervisor",
        },
      }),
    );

    await expect(tryRefreshOnce()).resolves.toBe(true);
    expect(getAccessToken()).toBe("new-token");
    const request = vi.mocked(globalThis.fetch).mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(request.headers).get("X-Client-App")).toBe("admin");
    expect(getCachedUser()).toMatchObject({
      roles: ["editor"],
      platformRole: "editor",
      tenantId: "tenant-a",
      csRole: "supervisor",
    });
  });

  it("登录请求声明 admin 客户端，服务端才能选择隔离 Cookie", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      result({ token: "admin-token", userInfo: { userId: 7 } }),
    );

    await login("alice", "password");

    const request = vi.mocked(globalThis.fetch).mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(request.body))).toMatchObject({ clientId: "admin" });
  });

  it("logout 无论请求失败都清 access token、user、过期标记并广播 WS 关闭", async () => {
    sessionStorage.setItem("agent.access_token", "token");
    sessionStorage.setItem("agent.user_info", JSON.stringify({ roles: ["admin"] }));
    sessionStorage.setItem("agent.session_expired", "1");
    const clear = vi.fn();
    const events: Event[] = [];
    const listener = (event: Event) => events.push(event);
    window.addEventListener("agent:logout", listener);
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new Error("offline"));

    await logout({ clear });

    expect(getAccessToken()).toBeNull();
    expect(getCachedUser()).toBeNull();
    expect(sessionStorage.getItem("agent.session_expired")).toBeNull();
    expect(clear).toHaveBeenCalledTimes(1);
    expect(events).toHaveLength(1);
    window.removeEventListener("agent:logout", listener);
  });
});
