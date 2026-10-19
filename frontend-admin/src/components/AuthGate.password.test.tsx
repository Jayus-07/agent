import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import AuthGate from "./AuthGate";

const state = vi.hoisted(() => ({ path: "/knowledge/workbench", temporary: true }));
vi.mock("next/navigation", () => ({ usePathname: () => state.path }));
vi.mock("@/lib/auth", () => ({
  atLeast: () => true,
  getAccessToken: () => "test-token",
  getCachedUser: () => ({ mustChangePassword: state.temporary }),
  tryRefreshOnce: vi.fn(),
}));

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true; });
let cleanup: (() => void) | undefined;
afterEach(() => { cleanup?.(); });

describe("首次改密路由门禁", () => {
  it.each([
    ["/knowledge/workbench", true, false, true],
    ["/change-password", true, true, false],
    ["/knowledge/workbench", false, true, false],
  ])("%s 待改密=%s", (path, temporary, visible, redirect) => {
    state.path = path;
    state.temporary = temporary;
    const navigate = vi.fn();
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    cleanup = () => { act(() => root.unmount()); container.remove(); };
    act(() => root.render(<AuthGate onNavigate={navigate}><span>受保护页面</span></AuthGate>));
    expect(container.textContent.includes("受保护页面")).toBe(visible);
    if (redirect) expect(navigate).toHaveBeenCalledWith("/change-password");
    else expect(navigate).not.toHaveBeenCalled();
  });
});
