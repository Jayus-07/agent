import { beforeEach, describe, expect, it, vi } from "vitest";

const fetcherMock = vi.hoisted(() => ({
  request: vi.fn(),
  requestSilent: vi.fn(),
}));

vi.mock("@/lib/fetcher", () => fetcherMock);

import {
  deleteMemorySession,
  getSessionMessages,
  listSessions,
  renameMemorySession,
} from "./memory";

describe("客服记忆 API 路由", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("会话读写都绑定客服产品入口", async () => {
    fetcherMock.request.mockResolvedValueOnce({ sessions: [] });
    await listSessions();
    expect(fetcherMock.request).toHaveBeenLastCalledWith(
      "/api/memory/customer-service/sessions",
    );

    fetcherMock.request.mockResolvedValueOnce({ messages: [] });
    await getSessionMessages("session /1");
    expect(fetcherMock.request).toHaveBeenLastCalledWith(
      "/api/memory/customer-service/sessions/session%20%2F1",
    );

    fetcherMock.request.mockResolvedValueOnce({ ok: true });
    await deleteMemorySession("session-1");
    expect(fetcherMock.request).toHaveBeenLastCalledWith(
      "/api/memory/customer-service/sessions/session-1",
      { method: "DELETE" },
    );

    fetcherMock.request.mockResolvedValueOnce({ ok: true });
    await renameMemorySession("session-1", "标题");
    expect(fetcherMock.request).toHaveBeenLastCalledWith(
      "/api/memory/customer-service/sessions/session-1",
      { method: "PATCH", body: JSON.stringify({ title: "标题" }) },
    );
  });
});
