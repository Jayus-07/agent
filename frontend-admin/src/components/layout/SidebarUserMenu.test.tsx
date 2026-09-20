import { act } from "react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import SidebarUserMenu from "./SidebarUserMenu";

beforeAll(() => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
});

const mounted: { container: HTMLElement; root: Root }[] = [];

function mount(
  queryClient?: { clear: () => void },
  onNavigate: (path: string) => void = vi.fn(),
) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <SidebarUserMenu
        queryClient={queryClient}
        onNavigate={onNavigate}
      />,
    );
  });
  mounted.push({ container, root });
  return container;
}

async function clickLogout(container: HTMLElement) {
  const trigger = container.querySelector<HTMLButtonElement>(
    '[aria-label="打开用户菜单"]',
  );
  expect(trigger).toBeTruthy();
  act(() => trigger!.click());
  const button = container.querySelector<HTMLButtonElement>(
    '[data-testid="logout-button"]',
  );
  expect(button).toBeTruthy();
  await act(async () => {
    button!.click();
    button!.click();
    await Promise.resolve();
  });
}

beforeEach(() => {
  sessionStorage.clear();
  sessionStorage.setItem("agent.access_token", "access-token");
  sessionStorage.setItem(
    "agent.user_info",
    JSON.stringify({ userId: 7, realName: "管理员", roles: ["admin"] }),
  );
  sessionStorage.setItem("agent.session_expired", "1");
  vi.restoreAllMocks();
});

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount());
    container.remove();
  }
  sessionStorage.clear();
});

describe("SidebarUserMenu", () => {
  it("admin 显示访问控制入口，viewer/editor 不显示", () => {
    const admin = mount();
    expect(admin.querySelector('a[href="/settings/access"]')).toBeNull();
    act(() =>
      admin.querySelector<HTMLButtonElement>('[aria-label="打开用户菜单"]')!.click(),
    );
    expect(admin.querySelector('a[href="/settings/access"]')).toBeTruthy();

    act(() => admin.querySelector<HTMLButtonElement>('[aria-label="打开用户菜单"]')!.click());
    sessionStorage.clear();
    sessionStorage.setItem(
      "agent.user_info",
      JSON.stringify({ userId: 8, realName: "编辑", roles: ["editor"] }),
    );
    const editor = mount();
    act(() =>
      editor.querySelector<HTMLButtonElement>('[aria-label="打开用户菜单"]')!.click(),
    );
    expect(editor.querySelector('a[href="/settings/access"]')).toBeNull();
  });

  it("logout 成功只请求一次，清理全部本地态、query cache、WS 事件并导航一次", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ code: 200, data: true })));
    const clear = vi.fn();
    const navigate = vi.fn();
    const logoutEvents: Event[] = [];
    const listener = (event: Event) => logoutEvents.push(event);
    window.addEventListener("agent:logout", listener);

    const container = mount({ clear }, navigate);
    // 用测试注入的导航函数覆盖默认 window.location.assign。
    const menu = container.querySelector<HTMLElement>("[data-testid=sidebar-user-menu]");
    expect(menu).toBeTruthy();
    await clickLogout(container);

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy.mock.calls[0][0]).toBe("/api/auth/logout");
    expect(sessionStorage.getItem("agent.access_token")).toBeNull();
    expect(sessionStorage.getItem("agent.user_info")).toBeNull();
    expect(sessionStorage.getItem("agent.session_expired")).toBeNull();
    expect(clear).toHaveBeenCalledTimes(1);
    expect(logoutEvents).toHaveLength(1);
    expect(navigate).toHaveBeenCalledTimes(1);
    expect(navigate).toHaveBeenCalledWith("/login");

    window.removeEventListener("agent:logout", listener);
  });

  it("logout 失败仍清理并只导航一次", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("offline"));
    const clear = vi.fn();
    const navigate = vi.fn();
    const container = mount({ clear }, navigate);
    await clickLogout(container);
    expect(clear).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem("agent.access_token")).toBeNull();
    expect(sessionStorage.getItem("agent.user_info")).toBeNull();
    expect(sessionStorage.getItem("agent.session_expired")).toBeNull();
    expect(navigate).toHaveBeenCalledTimes(1);
  });
});
