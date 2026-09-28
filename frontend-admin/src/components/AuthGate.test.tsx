import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

const nav = vi.hoisted(() => ({ pathname: "/settings/access" }));
const auth = vi.hoisted(() => ({
  getAccessToken: vi.fn<() => string | null>(() => "token"),
  tryRefreshOnce: vi.fn<() => Promise<boolean>>(async () => false),
  atLeast: vi.fn<(role: "admin") => boolean>(() => false),
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
});

afterEach(() => {
  if (!mounted) return;
  act(() => mounted?.root.unmount());
  mounted.container.remove();
  mounted = null;
});

function mount() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => root.render(<AuthGate><span>admin-page</span></AuthGate>));
  mounted = { container, root };
  return container;
}

describe("管理端 AuthGate", () => {
  it("没有平台管理员角色时不渲染后台内容", () => {
    const container = mount();

    expect(container.textContent).toContain("无管理端访问权限");
    expect(container.textContent).not.toContain("admin-page");
  });

  it("super_admin 满足 admin 等级时渲染后台内容", () => {
    auth.atLeast.mockReturnValue(true);
    const container = mount();

    expect(container.textContent).toContain("admin-page");
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });
});
