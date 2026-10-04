import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

const nav = vi.hoisted(() => ({ pathname: "/cs" }));
const auth = vi.hoisted(() => ({
  getAccessToken: vi.fn<() => string | null>(() => "token"),
  tryRefreshOnce: vi.fn<() => Promise<boolean>>(async () => false),
  getCsRole: vi.fn<() => "agent" | "supervisor" | null>(() => null),
  getCachedUser: vi.fn<() => Record<string, unknown> | null>(() => null),
  logout: vi.fn<() => Promise<void>>(async () => {}),
}));

vi.mock("next/navigation", () => ({ usePathname: () => nav.pathname }));
vi.mock("@/lib/auth", () => auth);

import AuthGate from "./AuthGate";

beforeAll(() => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
});

let mounted: { container: HTMLElement; root: Root } | null = null;

beforeEach(() => {
  auth.getAccessToken.mockReturnValue("token");
  auth.tryRefreshOnce.mockResolvedValue(false);
  auth.getCsRole.mockReturnValue(null);
  auth.getCachedUser.mockReturnValue(null);
  auth.logout.mockResolvedValue();
});

afterEach(() => {
  if (!mounted) return;
  act(() => mounted?.root.unmount());
  mounted.container.remove();
  mounted = null;
});

function mount(onNavigate?: (path: string) => void) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() =>
    root.render(
      <AuthGate onNavigate={onNavigate}>
        <span>cs-page</span>
      </AuthGate>,
    ),
  );
  mounted = { container, root };
  return container;
}

describe("客服端 AuthGate", () => {
  it("没有 csRole 的 admin 不渲染客服工作台", () => {
    const container = mount();

    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    expect(container.textContent).toContain("无客服工作台访问权限");
    expect(container.textContent).not.toContain("cs-page");
  });

  it("403 页展示当前账号与角色并提供切换账号出口", async () => {
    auth.getCachedUser.mockReturnValue({
      username: "ordinary_user",
      roles: ["viewer"],
    });
    const navigate = vi.fn();
    const container = mount(navigate);

    expect(container.textContent).toContain("ordinary_user");
    expect(container.textContent).toContain("查看者");
    expect(container.textContent).toContain("未绑定坐席");

    const button = container.querySelector<HTMLButtonElement>("button");
    expect(button?.textContent).toContain("切换账号");
    await act(async () => {
      button!.click();
      await Promise.resolve();
    });
    expect(auth.logout).toHaveBeenCalledTimes(1);
    // 带 redirect 回跳参数：切号成功后由登录页送回原目标页
    expect(navigate).toHaveBeenCalledWith("/login?redirect=%2Fcs");
  });

  it("agent 可渲染客服工作台", () => {
    auth.getCsRole.mockReturnValue("agent");
    const container = mount();

    expect(container.textContent).toContain("cs-page");
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });
});
