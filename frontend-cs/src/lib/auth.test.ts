import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import {
  getCachedUser,
  getAccessToken,
  logout,
  tryRefreshOnce,
} from "./auth";
import {
  CS_AGENT_LOGOUT_EVENT,
  useAgentSocket,
} from "./csAgentWs";

const issueWsTicket = vi.hoisted(() => vi.fn());
vi.mock("@/api/cs", () => ({ issueWsTicket }));

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
    expect(getCachedUser()).toMatchObject({
      roles: ["editor"],
      platformRole: "editor",
      tenantId: "tenant-a",
      csRole: "supervisor",
    });
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

  it("客服 WS 收到 logout 事件后关闭当前连接且不再退避重连", async () => {
    vi.useFakeTimers();
    const sockets: Array<{ readyState: number; close: ReturnType<typeof vi.fn> }> = [];
    class FakeWebSocket {
      static readonly OPEN = 1;
      readyState = 0;
      onopen: (() => void) | null = null;
      onclose: (() => void) | null = null;
      close = vi.fn(() => {
        this.readyState = 3;
        this.onclose?.();
      });
      send = vi.fn();

      constructor(_url: string) {
        sockets.push(this);
        queueMicrotask(() => {
          this.readyState = FakeWebSocket.OPEN;
          this.onopen?.();
        });
      }
    }
    vi.stubGlobal("WebSocket", FakeWebSocket);
    issueWsTicket.mockResolvedValue({ ticket: "ticket", ws_path: "/ws/cs/agent" });

    function Probe() {
      useAgentSocket(() => undefined);
      return null;
    }

    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => {
      root.render(createElement(Probe));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(sockets).toHaveLength(1);

    act(() => window.dispatchEvent(new Event(CS_AGENT_LOGOUT_EVENT)));
    expect(sockets[0].close).toHaveBeenCalledTimes(1);
    await act(async () => {
      vi.advanceTimersByTime(60_000);
      await Promise.resolve();
    });
    expect(issueWsTicket).toHaveBeenCalledTimes(1);
    act(() => root.unmount());
    container.remove();
    vi.useRealTimers();
    issueWsTicket.mockReset();
  });
});
