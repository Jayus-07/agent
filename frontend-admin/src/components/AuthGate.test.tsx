import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

const nav = vi.hoisted(() => ({ pathname: "/settings/access" }));
const auth = vi.hoisted(() => ({
  getAccessToken: vi.fn<() => string | null>(() => "token"),
  tryRefreshOnce: vi.fn<() => Promise<boolean>>(async () => false),
  atLeast: vi.fn<(role: "admin") => boolean>(() => false),
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
  auth.atLeast.mockReturnValue(false);
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
        <span>admin-page</span>
      </AuthGate>,
    ),
  );
  mounted = { container, root };
  return container;
}

describe("管理端 AuthGate", () => {
  it("没有平台管理员角色时渲染整页 403，不渲染后台内容", () => {
    const container = mount();

    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    expect(container.textContent).toContain("无管理端访问权限");
    expect(container.textContent).not.toContain("admin-page");
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

    const button = container.querySelector<HTMLButtonElement>("button");
    expect(button?.textContent).toContain("切换账号");
    await act(async () => {
      button!.click();
      await Promise.resolve();
    });
    expect(auth.logout).toHaveBeenCalledTimes(1);
    // 带 redirect 回跳参数：切号成功后由登录页送回原目标页
    expect(navigate).toHaveBeenCalledWith("/login?redirect=%2Fsettings%2Faccess");
  });

  it("super_admin 满足 admin 等级时渲染后台内容", () => {
    auth.atLeast.mockReturnValue(true);
    const container = mount();

    expect(container.textContent).toContain("admin-page");
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });
});
