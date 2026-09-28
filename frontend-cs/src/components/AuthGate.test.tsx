import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

const nav = vi.hoisted(() => ({ pathname: "/cs" }));
const auth = vi.hoisted(() => ({
  getAccessToken: vi.fn<() => string | null>(() => "token"),
  tryRefreshOnce: vi.fn<() => Promise<boolean>>(async () => false),
  getCsRole: vi.fn<() => "agent" | "supervisor" | null>(() => null),
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
  act(() => root.render(<AuthGate><span>cs-page</span></AuthGate>));
  mounted = { container, root };
  return container;
}

describe("客服端 AuthGate", () => {
  it("没有 csRole 的 admin 不渲染客服工作台", () => {
    const container = mount();

    expect(container.textContent).toContain("无客服工作台访问权限");
    expect(container.textContent).not.toContain("cs-page");
  });

  it("agent 可渲染客服工作台", () => {
    auth.getCsRole.mockReturnValue("agent");
    const container = mount();

    expect(container.textContent).toContain("cs-page");
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });
});
