import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fetcherMock = vi.hoisted(() => ({
  request: vi.fn(),
  requestSilent: vi.fn(),
}));
const authMock = vi.hoisted(() => ({
  bearerHeaders: vi.fn(() => ({})),
  handleAuthFailure: vi.fn(),
  tryRefreshOnce: vi.fn(async () => false),
}));

vi.mock("@/lib/fetcher", () => fetcherMock);
vi.mock("@/lib/auth", () => authMock);

import { abortChat, streamChat } from "./chat";

describe("客服聊天 API 路由", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    delete process.env.NEXT_PUBLIC_API_URL;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("流式提问固定走客服产品入口", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      'event: done\ndata: {"seq":1}\n\n',
      { headers: { "Content-Type": "text/event-stream" } },
    ));
    vi.stubGlobal("fetch", fetchMock);

    const events = [];
    for await (const event of streamChat({
      question: "你好",
      session_id: "session-1",
      request_id: "request-1",
    })) {
      events.push(event);
    }

    expect(events.map((event) => event.event)).toEqual(["done"]);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/chat/customer-service/stream",
    );
  });

  it("中止请求固定走客服产品入口", async () => {
    await abortChat("session-1", "request-1");

    expect(fetcherMock.requestSilent).toHaveBeenCalledWith(
      "/api/chat/customer-service/abort",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
