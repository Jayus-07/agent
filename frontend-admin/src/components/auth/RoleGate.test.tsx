import { act } from "react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import AccessControlPage from "@/app/settings/access/page";
import { listRbacAudit, listRbacUsers, updateRbacUser } from "@/api/rbac";

vi.mock("@/api/rbac", () => ({
  listRbacAudit: vi.fn(),
  listRbacUsers: vi.fn(),
  updateRbacUser: vi.fn(),
}));

beforeAll(() => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
});

let mounted: { container: HTMLElement; root: Root } | null = null;

beforeEach(() => {
  sessionStorage.clear();
  vi.mocked(listRbacUsers).mockReset();
  vi.mocked(listRbacAudit).mockReset();
  vi.mocked(updateRbacUser).mockReset();
});

afterEach(() => {
  if (!mounted) return;
  act(() => mounted?.root.unmount());
  mounted.container.remove();
  mounted = null;
});

function renderPage() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => root.render(<AccessControlPage />));
  mounted = { container, root };
  return container;
}

describe("访问控制页 RoleGate", () => {
  it.each(["viewer", "editor"])(
    "%s 直接访问显示现有 ForbiddenCard，且不请求用户或审计 API",
    (role) => {
      sessionStorage.setItem(
        "agent.user_info",
        JSON.stringify({ userId: 9, roles: [role] }),
      );
      const container = renderPage();
      expect(container.textContent).toContain("没有访问「访问控制」的权限");
      expect(listRbacUsers).not.toHaveBeenCalled();
      expect(listRbacAudit).not.toHaveBeenCalled();
    },
  );

  it("admin 加载用户并保存携带 version；409 刷新列表且不把 agentId 等敏感字段放进 DOM", async () => {
    sessionStorage.setItem(
      "agent.user_info",
      JSON.stringify({ userId: 1, roles: ["admin"] }),
    );
    const page = {
      items: [
        {
          userId: 7,
          username: "alice",
          realName: "Alice",
          dept: "客服",
          platformRole: "editor" as const,
          status: 1,
          version: 8,
          sessionCount: 2,
          csAgent: {
            agentId: "secret-agent-id",
            displayName: "Alice",
            role: "agent" as const,
            maxConversations: 4,
            enabled: true,
            accepting: true,
          },
        },
      ],
      total: 1,
      page: 1,
      pageSize: 20,
    };
    vi.mocked(listRbacUsers).mockResolvedValue(page);
    const container = renderPage();
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(container.textContent).toContain("Alice");
    expect(container.textContent).not.toContain("secret-agent-id");
    expect(container.textContent).not.toContain("password_hash");
    expect(container.textContent).not.toContain("access-token");

    vi.mocked(updateRbacUser).mockRejectedValueOnce({ status: 409 });
    vi.mocked(listRbacUsers).mockResolvedValueOnce(page);
    const save = container.querySelector<HTMLButtonElement>("button");
    const saveButton = Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("保存"));
    expect(save).toBeTruthy();
    expect(saveButton).toBeTruthy();
    await act(async () => {
      saveButton!.click();
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(updateRbacUser).toHaveBeenCalledWith(7, expect.objectContaining({ version: 8 }));
    expect(listRbacUsers).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain("保存冲突");
  });
});
